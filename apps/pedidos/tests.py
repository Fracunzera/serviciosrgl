import shutil
import tempfile
from datetime import time, timedelta
from io import BytesIO, StringIO
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.core.models import Barrio, City
from apps.directorio.models import Oficio, Rubro
from apps.resenas.models import Resena

from . import links, notificaciones, ruteo
from .models import Derivacion, Mensaje, Pedido
from .whatsapp import numero_wa


def guardia_siempre(oficio):
    for dia in range(7):
        oficio.franjas.create(dia=dia, desde=time(0), hasta=time(0))


@override_settings(ADMIN_EMAIL="admin@guia.test", NOTIFICADORES=["email", "semimanual"])
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("cargar_base", stdout=StringIO())
        cls.city = City.objects.get(slug="rio-gallegos")
        cls.centro = Barrio.objects.get(city=cls.city, slug="centro")
        cls.evita = Barrio.objects.get(city=cls.city, slug="evita")
        cls.plomeria = Rubro.objects.get(slug="plomeria")
        cls.gas = Rubro.objects.get(slug="gas")

    n = 0

    def oficio(self, nombre, rubro=None, barrios=None, guardia=False, **kw):
        Base.n += 1
        datos = dict(
            city=self.city, nombre=nombre, telefono=f"2966 15-40{Base.n:04d}", email=f"o{Base.n}@x.test",
            estado=Oficio.Estado.APROBADO, cubre_toda_la_ciudad=barrios is None, de_guardia=guardia,
        )
        datos.update(kw)
        o = Oficio.objects.create(**datos)
        o.rubros.add(rubro or self.plomeria)
        if barrios:
            o.barrios.set(barrios)
        if guardia:
            guardia_siempre(o)
        return o

    def firma(self, d):
        return links.link_derivacion(d).rstrip("/").rsplit("/", 1)[1]

    def pedido(self, urgencia=False, barrio=None, rubro=None, **kw):
        datos = dict(
            city=self.city, rubro=rubro or self.plomeria, barrio=barrio or self.centro,
            descripcion="Pierde el caño de la cocina", es_urgencia=urgencia,
            nombre_vecino="Marta Gómez", telefono_vecino="2966 15-999999",
            email_vecino="marta@x.test", direccion="Zapiola 123",
        )
        datos.update(kw)
        return Pedido.objects.create(**datos)


class WhatsappTests(TestCase):
    def test_formatos_locales(self):
        for tel in ["2966 15-123456", "(02966) 15 123456", "15-123456", "123456", "+54 9 2966 123456", "5492966123456"]:
            self.assertEqual(numero_wa(tel), "5492966123456", tel)

    def test_invalido(self):
        self.assertEqual(numero_wa("123"), "")


class DetectarRubroTests(Base):
    def test_detecta(self):
        rubros = list(Rubro.objects.filter(en_urgencias=True))
        casos = {
            "hay olor a gas en la cocina": "gas",
            "se rompió el caño y sale agua por todos lados": "plomeria",
            "me quedé afuera y la llave quedó adentro": "cerrajeria",
            "salta la térmica y no hay luz": "electricidad",
        }
        for texto, slug in casos.items():
            self.assertEqual(ruteo.detectar_rubro(texto, rubros).slug, slug, texto)
        self.assertIsNone(ruteo.detectar_rubro("necesito ayuda", rubros))


class PresupuestoTests(Base):
    def test_fan_out_solo_a_la_zona_y_disponibles(self):
        toda = self.oficio("Toda la ciudad")
        centro = self.oficio("Del centro", barrios=[self.centro])
        self.oficio("De Evita", barrios=[self.evita])
        self.oficio("Ausente", no_disponible_hasta=timezone.now() + timedelta(days=2))
        self.oficio("Pendiente", estado=Oficio.Estado.PENDIENTE)
        self.oficio("Gasista", rubro=self.gas)

        p = self.pedido()
        self.assertEqual(ruteo.derivar_presupuesto(p), 2)
        self.assertEqual({d.oficio for d in p.derivaciones.all()}, {toda, centro})
        # Por oficio: mail + copia semimanual al admin; más el mail al vecino.
        self.assertEqual(Mensaje.objects.filter(destino=toda.email).count(), 1)
        self.assertEqual(Mensaje.objects.filter(destino="admin@guia.test").count(), 2)
        self.assertTrue(Mensaje.objects.filter(destino="marta@x.test").exists())
        copia = Mensaje.objects.filter(destino="admin@guia.test").first().texto
        self.assertIn("wa.me/5492966", copia)
        self.assertIn("Pierde el caño de la cocina", copia)  # el texto completo, para leerlo sin abrir WhatsApp

    def test_sin_oficios_avisa_al_admin(self):
        p = self.pedido()
        self.assertEqual(ruteo.derivar_presupuesto(p), 0)
        self.assertTrue(Mensaje.objects.filter(asunto__startswith="[Sin oficios]").exists())

    def test_responder_y_elegir(self):
        a, b = self.oficio("A"), self.oficio("B")
        p = self.pedido()
        ruteo.derivar_presupuesto(p)
        da, db = p.derivaciones.get(oficio=a), p.derivaciones.get(oficio=b)
        self.assertTrue(ruteo.responder_presupuesto(da, "Voy mañana", 15000))
        self.assertFalse(ruteo.responder_presupuesto(da, "otra vez", None))  # ya respondió
        ruteo.responder_presupuesto(db, "Puedo el lunes")

        self.assertTrue(ruteo.elegir(p, db.pk))
        p.refresh_from_db()
        self.assertEqual((p.estado, p.oficio_asignado), (Pedido.Estado.ASIGNADO, b))
        self.assertFalse(ruteo.elegir(p, da.pk))  # ya asignado
        self.assertIsNotNone(Oficio.objects.get(pk=b.pk).ultima_asignacion)

        # El elegido ve el teléfono del vecino; el otro no.
        r_b = self.client.get(reverse("pedidos:derivacion", args=[self.firma(db)]))
        r_a = self.client.get(reverse("pedidos:derivacion", args=[self.firma(da)]))
        self.assertContains(r_b, "2966 15-999999")
        self.assertNotContains(r_a, "2966 15-999999")
        self.assertContains(r_a, "lo tomó otro oficio")


class UrgenciaTests(Base):
    def test_escalado_completo_con_aceptacion_tarde(self):
        ahora = timezone.now()
        viejo = self.oficio("Asignado hace mucho", guardia=True, ultima_asignacion=ahora - timedelta(days=30))
        nunca = self.oficio("Nunca asignado", guardia=True)
        reciente = self.oficio("Asignado ayer", guardia=True, ultima_asignacion=ahora - timedelta(days=1))
        self.oficio("Sin guardia")
        p = self.pedido(urgencia=True)

        d1 = ruteo.escalar(p.pk, ahora)
        self.assertEqual((d1.oficio, d1.posicion), (nunca, 1))  # nunca asignado = primero
        self.assertIsNone(ruteo.escalar(p.pk, ahora + timedelta(minutes=5)))  # plazo corriendo

        d2 = ruteo.escalar(p.pk, ahora + timedelta(minutes=11))
        self.assertEqual(d2.oficio, viejo)
        d1.refresh_from_db()
        self.assertEqual(d1.estado, Derivacion.Estado.VENCIDA)

        d3 = ruteo.escalar(p.pk, ahora + timedelta(minutes=22))
        self.assertEqual(d3.oficio, reciente)

        self.assertIsNone(ruteo.escalar(p.pk, ahora + timedelta(minutes=33)))
        p.refresh_from_db()
        self.assertIsNotNone(p.fallback_at)
        self.assertTrue(Mensaje.objects.filter(asunto__startswith="[URGENCIA SIN CUBRIR]").exists())

        # El primero acepta tarde: vale, porque nadie la tomó.
        self.assertTrue(ruteo.aceptar_urgencia(d1))
        p.refresh_from_db()
        self.assertEqual((p.estado, p.oficio_asignado), (Pedido.Estado.ASIGNADO, nunca))
        self.assertFalse(ruteo.aceptar_urgencia(d2))  # ya la tomó otro

    def test_prioriza_la_zona(self):
        fuera = self.oficio("Solo Evita", guardia=True, barrios=[self.evita])
        zona = self.oficio("Centro", guardia=True, barrios=[self.centro], ultima_asignacion=timezone.now())
        p = self.pedido(urgencia=True, barrio=self.centro)
        self.assertEqual(ruteo.escalar(p.pk).oficio, zona)
        self.assertEqual(ruteo.cola_urgencia(p), [fuera])

    def test_rechazo_escala_al_toque(self):
        a = self.oficio("A", guardia=True)
        b = self.oficio("B", guardia=True, ultima_asignacion=timezone.now())
        p = self.pedido(urgencia=True)
        d = ruteo.escalar(p.pk)
        self.assertEqual(d.oficio, a)
        ruteo.rechazar(d)
        self.assertEqual(p.derivaciones.get(estado=Derivacion.Estado.ENVIADA).oficio, b)

    def test_fuera_de_franja_va_directo_al_fallback(self):
        o = self.oficio("Solo sábados", de_guardia=True, telefono_en_fallback=True)
        o.franjas.create(dia=5, desde=time(8), hasta=time(9))
        p = self.pedido(urgencia=True)
        with mock.patch.object(Oficio, "de_guardia_en", return_value=False):
            self.assertIsNone(ruteo.escalar(p.pk))
        p.refresh_from_db()
        self.assertIsNotNone(p.fallback_at)
        # Fallback: figura con teléfono porque dio consentimiento.
        r = self.client.get(reverse("pedidos:seguimiento", args=[p.token]))
        self.assertContains(r, o.telefono)

    def test_fallback_no_muestra_sin_consentimiento(self):
        o = self.oficio("Sin consentimiento", guardia=True)
        p = self.pedido(urgencia=True)
        p.fallback_at = timezone.now()
        p.save()
        self.assertNotContains(self.client.get(reverse("pedidos:seguimiento", args=[p.token])), o.telefono)

    def test_worker_tick(self):
        self.oficio("A", guardia=True)
        p = self.pedido(urgencia=True)
        call_command("worker", "--una-vez", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(p.derivaciones.count(), 1)
        self.assertTrue(len(mail.outbox) >= 1)  # salió por la bandeja


class LinksTests(Base):
    def test_get_no_cambia_nada_y_post_acepta(self):
        o = self.oficio("A", guardia=True)
        p = self.pedido(urgencia=True)
        d = ruteo.escalar(p.pk)
        url = reverse("pedidos:derivacion", args=[self.firma(d)])
        r = self.client.get(url)
        self.assertContains(r, "Acepto, voy")
        self.assertNotContains(r, "2966 15-999999")  # sin contacto antes de aceptar
        p.refresh_from_db()
        self.assertEqual(p.estado, Pedido.Estado.ABIERTO)

        r = self.client.post(url, {"accion": "aceptar"}, follow=True)
        self.assertContains(r, "2966 15-999999")
        self.assertContains(r, "Zapiola 123")
        p.refresh_from_db()
        self.assertEqual(p.oficio_asignado, o)

    def test_firma_adulterada_o_revocada(self):
        self.oficio("A", guardia=True)
        d = ruteo.escalar(self.pedido(urgencia=True).pk)
        firma = self.firma(d)
        self.assertEqual(self.client.get(reverse("pedidos:derivacion", args=[firma + "x"])).status_code, 404)
        Oficio.objects.filter(pk=d.oficio_id).update(version_links=2)
        self.assertEqual(self.client.get(reverse("pedidos:derivacion", args=[firma])).status_code, 404)

    def test_suspendido_no_puede_aceptar(self):
        self.oficio("A", guardia=True)
        p = self.pedido(urgencia=True)
        d = ruteo.escalar(p.pk)
        Oficio.objects.filter(pk=d.oficio_id).update(estado=Oficio.Estado.SUSPENDIDO)
        self.client.post(reverse("pedidos:derivacion", args=[self.firma(d)]), {"accion": "aceptar"})
        p.refresh_from_db()
        self.assertEqual(p.estado, Pedido.Estado.ABIERTO)

    def test_disponibilidad(self):
        o = self.oficio("A", guardia=True)
        firma = links.link_disponibilidad(o).rstrip("/").rsplit("/", 1)[1]
        url = reverse("pedidos:disponibilidad", args=[firma])
        hasta = timezone.localdate() + timedelta(days=3)
        self.client.post(url, {"accion": "ausente", "hasta": hasta.isoformat()})
        o.refresh_from_db()
        self.assertEqual(timezone.localtime(o.no_disponible_hasta).date(), hasta + timedelta(days=1))
        self.assertEqual(ruteo.cola_urgencia(self.pedido(urgencia=True)), [])
        self.client.post(url, {"accion": "disponible"})
        o.refresh_from_db()
        self.assertIsNone(o.no_disponible_hasta)


class CierreYResenaTests(Base):
    def asignado(self, hace_horas=0):
        o = self.oficio("A", guardia=True)
        p = self.pedido(urgencia=True)
        ruteo.aceptar_urgencia(ruteo.escalar(p.pk))
        Pedido.objects.filter(pk=p.pk).update(asignado_at=timezone.now() - timedelta(hours=hace_horas))
        return Pedido.objects.get(pk=p.pk), o

    def test_autocompletar_a_las_24h(self):
        p, _ = self.asignado(hace_horas=23)
        self.assertEqual(ruteo.autocompletar(), 0)
        Pedido.objects.filter(pk=p.pk).update(asignado_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(ruteo.autocompletar(), 1)
        p.refresh_from_db()
        self.assertEqual((p.estado, p.completado_por), (Pedido.Estado.COMPLETADO, Pedido.Completado.AUTO))
        self.assertTrue(Mensaje.objects.filter(asunto__startswith="¿Cómo te fue").exists())

    def test_vecino_confirma_y_resena_una_sola_vez(self):
        p, o = self.asignado()
        url_resena = reverse("resenas:resena", args=[p.token])
        self.assertRedirects(self.client.get(url_resena), reverse("pedidos:seguimiento", args=[p.token]))

        self.client.post(reverse("pedidos:seguimiento", args=[p.token]), {"accion": "confirmar"})
        p.refresh_from_db()
        self.assertEqual(p.completado_por, Pedido.Completado.VECINO)

        self.client.post(url_resena, {"estrellas": 5, "texto": "Impecable", "autor": "Marta, B° Centro"})
        r = Resena.objects.get(pedido=p)
        self.assertEqual((r.oficio, r.estrellas, r.bootstrap), (o, 5, False))
        self.client.post(url_resena, {"estrellas": 1, "texto": "otra", "autor": "x"})
        self.assertEqual(Resena.objects.filter(oficio=o).count(), 1)
        self.assertContains(self.client.get(reverse("directorio:oficio", args=[self.city.slug, o.slug])), "Impecable")

    def test_reclamo_avisa_al_admin(self):
        p, _ = self.asignado()
        self.client.post(reverse("resenas:reclamo", args=[p.token]), {"texto": "No vino nunca"})
        self.assertTrue(p.reclamos.exists())
        self.assertTrue(Mensaje.objects.filter(asunto__startswith="[Reclamo]").exists())

    def test_cerrar_viejos(self):
        p = self.pedido()
        Pedido.objects.filter(pk=p.pk).update(creado_at=timezone.now() - timedelta(days=31))
        self.assertEqual(ruteo.cerrar_viejos(), 1)
        p.refresh_from_db()
        self.assertEqual(p.motivo_cierre, Pedido.MotivoCierre.SIN_RESPUESTA)


class BandejaTests(Base):
    def test_envia_y_reintenta(self):
        m = Mensaje.objects.create(destino="a@x.test", asunto="Hola", texto="texto")
        self.assertEqual(notificaciones.enviar_pendientes(), 1)
        m.refresh_from_db()
        self.assertEqual(m.estado, Mensaje.Estado.ENVIADO)
        self.assertEqual(mail.outbox[0].subject, "Hola")

    def test_falla_con_backoff_y_se_rinde(self):
        m = Mensaje.objects.create(destino="a@x.test", asunto="Hola", texto="texto")
        with mock.patch.dict(notificaciones.SENDERS, {"email": mock.Mock(side_effect=OSError("smtp caído"))}):
            notificaciones.enviar_pendientes()
            m.refresh_from_db()
            self.assertEqual((m.intentos, m.estado), (1, Mensaje.Estado.PENDIENTE))
            self.assertGreater(m.proximo_intento_at, timezone.now() + timedelta(minutes=1))
            for _ in range(5):
                Mensaje.objects.filter(pk=m.pk).update(proximo_intento_at=timezone.now())
                notificaciones.enviar_pendientes()
        m.refresh_from_db()
        self.assertEqual(m.estado, Mensaje.Estado.FALLIDO)
        self.assertIn("smtp caído", m.ultimo_error)


class FormulariosTests(Base):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media)

    def datos(self, **kw):
        d = dict(barrio=self.centro.pk, descripcion="Hay olor a gas en la cocina", nombre_vecino="Juan",
                 telefono_vecino="2966 15-111111", direccion="Roca 1", email_vecino="", sitio_web="")
        d.update(kw)
        return d

    def test_urgencia_detecta_rubro_y_redirige_al_seguimiento(self):
        self.oficio("Gasista", rubro=self.gas, guardia=True)
        r = self.client.post(reverse("pedidos:urgencia", args=["rio-gallegos"]), self.datos())
        p = Pedido.objects.get()
        self.assertRedirects(r, reverse("pedidos:seguimiento", args=[p.token]))
        self.assertEqual((p.rubro, p.es_urgencia), (self.gas, True))
        self.assertEqual(p.derivaciones.count(), 1)

    def test_urgencia_sin_rubro_detectable(self):
        r = self.client.post(reverse("pedidos:urgencia", args=["rio-gallegos"]), self.datos(descripcion="ayuda"))
        self.assertContains(r, "elegilo de la lista")

    def test_honeypot_y_limite_por_telefono(self):
        url = reverse("pedidos:urgencia", args=["rio-gallegos"])
        self.client.post(url, self.datos(sitio_web="http://spam"))
        self.assertEqual(Pedido.objects.count(), 0)
        for _ in range(4):
            self.client.post(url, self.datos())
        self.assertEqual(Pedido.objects.count(), 3)

    def test_presupuesto_exige_mail(self):
        r = self.client.post(reverse("pedidos:presupuesto", args=["rio-gallegos"]),
                             self.datos(rubro=self.plomeria.pk))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_presupuesto_directo_a_un_oficio(self):
        elegido = self.oficio("Elegido")
        self.oficio("Otro")
        self.client.post(reverse("pedidos:presupuesto", args=["rio-gallegos"]),
                         self.datos(rubro=self.plomeria.pk, email_vecino="j@x.test", oficio=elegido.slug))
        self.assertEqual([d.oficio for d in Pedido.objects.get().derivaciones.all()], [elegido])

    def test_foto_se_achica_y_pierde_el_exif(self):
        im = Image.new("RGB", (4000, 3000), "red")
        exif = Image.Exif()
        exif[0x8825] = {2: (51.0, 37.0, 0.0)}  # GPSInfo
        buf = BytesIO()
        im.save(buf, "JPEG", exif=exif)
        foto = SimpleUploadedFile("casa.jpg", buf.getvalue(), content_type="image/jpeg")
        with override_settings(MEDIA_ROOT=self.media):
            self.client.post(reverse("pedidos:presupuesto", args=["rio-gallegos"]),
                             self.datos(rubro=self.plomeria.pk, email_vecino="j@x.test", fotos=[foto]))
            guardada = Pedido.objects.get().fotos.get()
            with Image.open(guardada.imagen.path) as im2:
                self.assertEqual(max(im2.size), 1600)
                self.assertEqual(len(im2.getexif()), 0)
