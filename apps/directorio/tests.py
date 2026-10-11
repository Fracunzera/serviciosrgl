from datetime import datetime, time, timedelta, timezone as dt_tz
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.core.models import City
from apps.directorio.models import Consentimiento, FranjaGuardia, Oficio

RGL = ZoneInfo("America/Argentina/Rio_Gallegos")


def local(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=RGL)


# 2026-10-10 es sábado.
SAB = (2026, 10, 10)


class FranjaGuardiaTests(TestCase):
    def franja(self, dia, desde, hasta):
        return FranjaGuardia(dia=dia, desde=time(*desde), hasta=time(*hasta))

    def test_franja_normal(self):
        f = self.franja(FranjaGuardia.Dia.SABADO, (8,), (22,))
        self.assertTrue(f.cubre(local(*SAB, 8)))
        self.assertTrue(f.cubre(local(*SAB, 21, 59)))
        self.assertFalse(f.cubre(local(*SAB, 22)))  # hasta es exclusivo
        self.assertFalse(f.cubre(local(*SAB, 7, 59)))
        self.assertFalse(f.cubre(local(2026, 10, 11, 12)))  # domingo

    def test_franja_que_cruza_medianoche(self):
        f = self.franja(FranjaGuardia.Dia.VIERNES, (22,), (6,))
        self.assertTrue(f.cubre(local(2026, 10, 9, 23)))  # viernes 23
        self.assertTrue(f.cubre(local(*SAB, 5, 59)))  # sábado 05:59
        self.assertFalse(f.cubre(local(*SAB, 6)))
        self.assertFalse(f.cubre(local(2026, 10, 9, 21)))

    def test_domingo_a_lunes(self):
        f = self.franja(FranjaGuardia.Dia.DOMINGO, (20,), (2,))
        self.assertTrue(f.cubre(local(2026, 10, 12, 1)))  # lunes 01:00

    def test_dia_completo(self):
        f = self.franja(FranjaGuardia.Dia.DOMINGO, (0,), (0,))
        self.assertTrue(f.cubre(local(2026, 10, 11, 0)))
        self.assertTrue(f.cubre(local(2026, 10, 11, 23, 59)))
        self.assertFalse(f.cubre(local(*SAB, 23, 59)))


class OficioTests(TestCase):
    def setUp(self):
        self.city = City.objects.create(nombre="Río Gallegos", slug="rio-gallegos")
        self.oficio = Oficio.objects.create(
            city=self.city, nombre="Juan Pérez", telefono="2966000000", email="j@x.com",
            estado=Oficio.Estado.APROBADO, de_guardia=True,
        )
        self.oficio.franjas.create(dia=FranjaGuardia.Dia.SABADO, desde=time(8), hasta=time(22))

    def test_de_guardia_convierte_utc_a_hora_local(self):
        # Sábado 23:30 UTC = sábado 20:30 en Río Gallegos (UTC-3): de guardia.
        self.assertTrue(self.oficio.de_guardia_en(datetime(2026, 10, 10, 23, 30, tzinfo=dt_tz.utc)))
        # Domingo 02:00 UTC = sábado 23:00 local: fuera de franja.
        self.assertFalse(self.oficio.de_guardia_en(datetime(2026, 10, 11, 2, 0, tzinfo=dt_tz.utc)))

    def test_no_disponible_hasta(self):
        ahora = local(*SAB, 10)
        self.oficio.no_disponible_hasta = ahora + timedelta(hours=1)
        self.assertFalse(self.oficio.de_guardia_en(ahora))
        self.assertTrue(self.oficio.de_guardia_en(ahora + timedelta(hours=2)))

    def test_suspendido_o_pendiente_no_recibe(self):
        for estado in (Oficio.Estado.SUSPENDIDO, Oficio.Estado.PENDIENTE):
            self.oficio.estado = estado
            self.assertFalse(self.oficio.de_guardia_en(local(*SAB, 10)))

    def test_slug_unico_por_ciudad(self):
        otro = Oficio.objects.create(city=self.city, nombre="Juan Pérez", telefono="1", email="o@x.com")
        self.assertEqual(self.oficio.slug, "juan-perez")
        self.assertEqual(otro.slug, "juan-perez-2")

    def test_verificado_requiere_aprobado_y_dni(self):
        self.assertFalse(self.oficio.verificado)
        self.oficio.dni_verificado_at = local(*SAB, 10)
        self.assertTrue(self.oficio.verificado)

    def test_consentimiento_actualiza_cache_y_deja_historial(self):
        tipo = Consentimiento.Tipo.TELEFONO_FALLBACK
        Consentimiento.registrar(self.oficio, tipo, True, "texto v1")
        self.assertTrue(Oficio.objects.get(pk=self.oficio.pk).telefono_en_fallback)
        Consentimiento.registrar(self.oficio, tipo, False, "texto v1")
        self.assertFalse(Oficio.objects.get(pk=self.oficio.pk).telefono_en_fallback)
        self.assertEqual(self.oficio.consentimientos.count(), 2)
