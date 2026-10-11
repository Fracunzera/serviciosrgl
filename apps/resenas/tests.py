from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from apps.core.models import Barrio, City
from apps.directorio.models import Oficio, Rubro
from apps.pedidos.models import Pedido
from apps.resenas.models import Resena


class ConstraintsTests(TestCase):
    def setUp(self):
        self.city = City.objects.create(nombre="Río Gallegos", slug="rio-gallegos")
        self.barrio = Barrio.objects.create(city=self.city, nombre="Centro", slug="centro")
        self.rubro = Rubro.objects.create(nombre="Plomería", slug="plomeria")
        self.oficio = Oficio.objects.create(city=self.city, nombre="Ana", telefono="1", email="a@x.com")

    def pedido(self, **kw):
        datos = dict(city=self.city, rubro=self.rubro, barrio=self.barrio, descripcion="Pierde la canilla",
                     nombre_vecino="Vecino", telefono_vecino="2", email_vecino="v@x.com")
        datos.update(kw)
        return Pedido(**datos)

    def test_pedido_asignado_sin_oficio_falla(self):
        with self.assertRaises(IntegrityError):
            self.pedido(estado=Pedido.Estado.ASIGNADO).save()

    def test_presupuesto_exige_email_urgencia_no(self):
        with self.assertRaises(ValidationError):
            self.pedido(email_vecino="").full_clean()
        self.pedido(email_vecino="", es_urgencia=True).full_clean()

    def test_barrio_de_otra_ciudad(self):
        otra = City.objects.create(nombre="Caleta Olivia", slug="caleta-olivia")
        with self.assertRaises(ValidationError):
            self.pedido(city=otra).full_clean()

    def test_token_de_seguimiento_unico(self):
        a, b = self.pedido(), self.pedido()
        a.save(); b.save()
        self.assertNotEqual(a.token, b.token)

    def test_resena_sin_pedido_solo_si_bootstrap(self):
        with self.assertRaises(IntegrityError):
            Resena.objects.create(oficio=self.oficio, autor="X", estrellas=5)

    def test_resena_estrellas_fuera_de_rango(self):
        with self.assertRaises(IntegrityError):
            Resena.objects.create(oficio=self.oficio, autor="X", estrellas=6, bootstrap=True)

    def test_resena_solo_de_pedido_completado_y_del_oficio_asignado(self):
        p = self.pedido(estado=Pedido.Estado.ASIGNADO, oficio_asignado=self.oficio)
        p.save()
        r = Resena(oficio=self.oficio, pedido=p, autor="X", estrellas=5)
        with self.assertRaises(ValidationError):
            r.full_clean()
        p.estado = Pedido.Estado.COMPLETADO
        p.save()
        r.full_clean()
        otro = Oficio.objects.create(city=self.city, nombre="Otro", telefono="3", email="o@x.com")
        with self.assertRaises(ValidationError):
            Resena(oficio=otro, pedido=p, autor="X", estrellas=5).full_clean()
