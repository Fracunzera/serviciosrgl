import shutil
import tempfile
from datetime import timedelta
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.directorio import acciones
from apps.directorio.models import Consentimiento, Oficio
from apps.pedidos import privacidad, ruteo
from apps.pedidos.models import Derivacion, FotoPedido, Mensaje, Pedido
from apps.pedidos.tests import Base
from apps.resenas.models import Reporte, Resena


def jpeg():
    buf = BytesIO()
    Image.new("RGB", (50, 50), "blue").save(buf, "JPEG")
    return SimpleUploadedFile("t.jpg", buf.getvalue(), content_type="image/jpeg")


class AccionesTests(Base):
    def test_aprobar_exige_matricula_en_gas(self):
        o = self.oficio("Gasista", rubro=self.gas, estado=Oficio.Estado.PENDIENTE)
        with self.assertRaises(acciones.NoSePuede):
            acciones.aprobar(o)
        o.matricula = "MG-1"
        acciones.aprobar(o)
        o.refresh_from_db()
        self.assertTrue(o.verificado)
        self.assertTrue(Mensaje.objects.filter(destino=o.email, asunto__startswith="Ya estás en la guía").exists())

    def test_suspender_libera_lo_pendiente_y_revoca_links(self):
        a = self.oficio("A", guardia=True)
        b = self.oficio("B", guardia=True, ultima_asignacion=timezone.now())
        urgencia = self.pedido(urgencia=True)
        d = ruteo.escalar(urgencia.pk)
        self.assertEqual(d.oficio, a)
        presupuesto = self.pedido()
        ruteo.derivar_presupuesto(presupuesto)
        Resena.objects.create(oficio=a, bootstrap=True, autor="X", estrellas=5)
        firma = self.firma(d)

        with self.assertRaises(acciones.NoSePuede):
            acciones.suspender(a, "  ")
        acciones.suspender(a, "No se presentó dos veces")

        a.refresh_from_db()
        self.assertEqual((a.estado, a.version_links), (Oficio.Estado.SUSPENDIDO, 2))
        self.assertEqual(self.client.get(reverse("pedidos:derivacion", args=[firma])).status_code, 404)
        d.refresh_from_db()
        self.assertEqual(d.estado, Derivacion.Estado.VENCIDA)
        # La urgencia no espera al suspendido: la próxima vuelta va a B.
        self.assertEqual(ruteo.escalar(urgencia.pk).oficio, b)
        self.assertEqual(presupuesto.derivaciones.get(oficio=a).estado, Derivacion.Estado.RECHAZADA)
        self.assertEqual(a.resenas.count(), 1)  # las reseñas quedan

    def test_reactivar_manda_links_nuevos(self):
        o = self.oficio("A")
        o.aprobado_at = timezone.now()
        o.save()
        acciones.suspender(o, "motivo")
        acciones.aprobar(o)
        self.assertTrue(Mensaje.objects.filter(destino=o.email, asunto__startswith="Tu perfil volvió").exists())


class AdminTests(Base):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@x.test", "x")
        self.client.force_login(self.admin)

    def test_tablero_para_revisar(self):
        self.oficio("Pendiente", estado=Oficio.Estado.PENDIENTE)
        p = self.pedido(urgencia=True)
        Pedido.objects.filter(pk=p.pk).update(fallback_at=timezone.now())
        r = self.client.get(reverse("admin:index"))
        self.assertContains(r, "Para revisar")
        filas = {i["texto"]: i["cantidad"] for i in r.context["para_revisar"]}
        self.assertEqual(filas["Oficios para aprobar"], 1)
        self.assertEqual(filas["Urgencias sin cubrir"], 1)
        # El link del tablero filtra bien.
        url = next(i["url"] for i in r.context["para_revisar"] if i["texto"] == "Urgencias sin cubrir")
        self.assertEqual(len(self.client.get(url).context["cl"].result_list), 1)

    def test_todas_las_pantallas_abren(self):
        o = self.oficio("A", guardia=True)
        p = self.pedido(urgencia=True)
        ruteo.escalar(p.pk)
        Resena.objects.create(oficio=o, bootstrap=True, autor="X", estrellas=4)
        for modelo in ["core_city", "directorio_rubro", "directorio_oficio", "pedidos_pedido",
                       "pedidos_mensaje", "resenas_resena", "resenas_reclamo", "resenas_reporte"]:
            self.assertEqual(self.client.get(reverse(f"admin:{modelo}_changelist")).status_code, 200, modelo)
        self.assertEqual(self.client.get(reverse("admin:directorio_oficio_change", args=[o.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:pedidos_pedido_change", args=[p.pk])).status_code, 200)

    def test_suspender_con_pagina_de_motivo(self):
        o = self.oficio("A")
        url = reverse("admin:directorio_oficio_changelist")
        datos = {"action": "suspender", "_selected_action": [o.pk]}
        r = self.client.post(url, datos)
        self.assertContains(r, "Vas a suspender")
        o.refresh_from_db()
        self.assertEqual(o.estado, Oficio.Estado.APROBADO)  # todavía no
        self.client.post(url, {**datos, "aplicar": "1", "motivo": "Cobró de más y no terminó"})
        o.refresh_from_db()
        self.assertEqual((o.estado, o.motivo_suspension), (Oficio.Estado.SUSPENDIDO, "Cobró de más y no terminó"))

    def test_aprobar_desde_la_lista(self):
        sin_mat = self.oficio("Gasista", rubro=self.gas, estado=Oficio.Estado.PENDIENTE)
        ok = self.oficio("Plomero", estado=Oficio.Estado.PENDIENTE)
        r = self.client.post(reverse("admin:directorio_oficio_changelist"),
                             {"action": "aprobar", "_selected_action": [sin_mat.pk, ok.pk]}, follow=True)
        self.assertContains(r, "falta la matrícula")
        self.assertEqual(Oficio.objects.get(pk=ok.pk).estado, Oficio.Estado.APROBADO)
        self.assertEqual(Oficio.objects.get(pk=sin_mat.pk).estado, Oficio.Estado.PENDIENTE)

    def test_form_no_deja_borrar_matricula_de_aprobado(self):
        o = self.oficio("Gasista", rubro=self.gas, matricula="MG-1")
        url = reverse("admin:directorio_oficio_change", args=[o.pk])
        datos = {
            "city": self.city.pk, "nombre": o.nombre, "slug": o.slug, "rubros": [self.gas.pk],
            "cubre_toda_la_ciudad": "on", "telefono": o.telefono, "email": o.email, "matricula": "",
            "franjas-TOTAL_FORMS": 0, "franjas-INITIAL_FORMS": 0,
            "fotos-TOTAL_FORMS": 0, "fotos-INITIAL_FORMS": 0,
            "consentimientos-TOTAL_FORMS": 0, "consentimientos-INITIAL_FORMS": 0,
        }
        r = self.client.post(url, datos)
        self.assertContains(r, "Obligatoria para Gas")
        o.refresh_from_db()
        self.assertEqual(o.matricula, "MG-1")

    def test_resena_bootstrap_desde_el_admin(self):
        o = self.oficio("A")
        self.client.post(reverse("admin:resenas_resena_add"), {
            "oficio": o.pk, "autor": "Consorcio Roca 300", "estrellas": 5, "texto": "Años trabajando con él",
            "creado_at_0": "2025-03-10", "creado_at_1": "10:00", "estado": "publicada",
        })
        r = Resena.objects.get()
        self.assertTrue(r.bootstrap)
        self.assertEqual(r.creado_at.year, 2025)

    def test_reintentar_mensaje_fallido(self):
        m = Mensaje.objects.create(destino="x@x.test", asunto="a", texto="b", estado="fallido", intentos=6)
        self.client.post(reverse("admin:pedidos_mensaje_changelist"), {"action": "reintentar", "_selected_action": [m.pk]})
        m.refresh_from_db()
        self.assertEqual((m.estado, m.intentos), ("pendiente", 0))


class AltaYReportesTests(Base):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media)

    def datos(self, **kw):
        d = {"nombre": "Luis Paz", "rubros": [self.plomeria.pk], "cubre_toda_la_ciudad": "on",
             "telefono": "2966 15-123456", "email": "luis@x.test", "acepto_terminos": "on",
             "franjas": ["sab", "dom"], "consiento_fallback": "on", "sitio_web": ""}
        d.update(kw)
        return d

    def test_alta_queda_pendiente_con_guardias_y_consentimientos(self):
        with override_settings(MEDIA_ROOT=self.media):
            r = self.client.post(reverse("directorio:anotate", args=["rio-gallegos"]), self.datos(fotos=[jpeg()]))
        self.assertRedirects(r, reverse("directorio:anotate_gracias", args=["rio-gallegos"]))
        o = Oficio.objects.get(email="luis@x.test")
        self.assertEqual(o.estado, Oficio.Estado.PENDIENTE)
        self.assertTrue(o.de_guardia)
        self.assertEqual(o.franjas.count(), 2)
        self.assertEqual(o.fotos.count(), 1)
        self.assertTrue(o.telefono_en_fallback)
        self.assertEqual(set(o.consentimientos.values_list("tipo", flat=True)),
                         {Consentimiento.Tipo.TERMINOS, Consentimiento.Tipo.TELEFONO_FALLBACK})
        self.assertTrue(Mensaje.objects.filter(asunto__startswith="[Para aprobar]").exists())
        # No figura hasta que se apruebe.
        self.assertNotContains(self.client.get(reverse("directorio:rubro", args=["rio-gallegos", "plomeria"])), "Luis Paz")

    def test_alta_valida_matricula_y_consentimiento(self):
        url = reverse("directorio:anotate", args=["rio-gallegos"])
        r = self.client.post(url, self.datos(rubros=[self.gas.pk]))
        self.assertContains(r, "la matrícula es obligatoria")
        r = self.client.post(url, self.datos(franjas=[]))
        self.assertContains(r, "Solo aplica si hacés guardias")
        r = self.client.post(url, self.datos(acepto_terminos=""))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Oficio.objects.exists())

    def test_sin_consentimiento_no_sale_en_fallback(self):
        self.client.post(reverse("directorio:anotate", args=["rio-gallegos"]), self.datos(consiento_fallback=""))
        o = Oficio.objects.get()
        self.assertFalse(o.telefono_en_fallback)
        self.assertEqual(o.consentimientos.count(), 1)

    def test_advertencia_vincula_al_oficio(self):
        o = self.oficio("Ramón Acosta")
        r = self.client.get(reverse("resenas:adverti", args=["rio-gallegos"]) + "?oficio=" + o.slug)
        self.assertContains(r, 'value="Ramón Acosta"')
        self.client.post(reverse("resenas:adverti", args=["rio-gallegos"]),
                         {"nombre_oficio": "Ramón Acosta", "texto": "No volvió más", "sitio_web": ""})
        rep = Reporte.objects.get()
        self.assertEqual((rep.tipo, rep.oficio), (Reporte.Tipo.ADVERTENCIA, o))
        self.assertTrue(Mensaje.objects.filter(asunto__startswith="[Advertencia]").exists())


class PrivacidadTests(Base):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media)

    def test_anonimiza_pedidos_viejos_y_conserva_la_resena(self):
        o = self.oficio("A")
        with override_settings(MEDIA_ROOT=self.media):
            viejo = self.pedido(estado=Pedido.Estado.COMPLETADO, oficio_asignado=o,
                                completado_at=timezone.now() - timedelta(days=400))
            foto = FotoPedido.objects.create(pedido=viejo, imagen=jpeg())
            ruta = foto.imagen.path
            Mensaje.objects.create(destino="x@x.test", asunto="a", texto="Marta Gómez 2966", pedido=viejo,
                                   estado="enviado")
            Resena.objects.create(oficio=o, pedido=viejo, autor="Marta, B° Centro", estrellas=5)
            reciente = self.pedido(estado=Pedido.Estado.COMPLETADO, oficio_asignado=o,
                                   completado_at=timezone.now() - timedelta(days=10))

            self.assertEqual(privacidad.anonimizar_viejos(), 1)

        viejo.refresh_from_db()
        self.assertEqual((viejo.telefono_vecino, viejo.email_vecino, viejo.direccion, viejo.descripcion), ("", "", "", ""))
        self.assertFalse(viejo.fotos.exists())
        self.assertFalse(Mensaje.objects.filter(pedido=viejo).exists())
        import os
        self.assertFalse(os.path.exists(ruta))
        self.assertTrue(Resena.objects.filter(pedido=viejo).exists())
        reciente.refresh_from_db()
        self.assertEqual(reciente.telefono_vecino, "2966 15-999999")
        self.assertEqual(privacidad.anonimizar_viejos(), 0)  # idempotente


class PaginasTests(Base):
    def test_legales_y_home(self):
        for nombre in ["nosotros", "contacto", "terminos", "privacidad"]:
            self.assertEqual(self.client.get(reverse(f"core:{nombre}", args=["rio-gallegos"])).status_code, 200)
        self.assertContains(self.client.get(reverse("core:privacidad", args=["rio-gallegos"])),
                            "AGENCIA DE ACCESO A LA INFORMACIÓN PÚBLICA")
        r = self.client.get(reverse("core:home", args=["rio-gallegos"]))
        self.assertContains(r, "CÓMO USAR ESTA GUÍA")
        self.assertContains(r, "SEGURIDAD Y CONFIANZA")
