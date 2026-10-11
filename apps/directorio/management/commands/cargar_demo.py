"""
Oficios y reseñas INVENTADOS para desarrollo. Nombres, teléfonos y
matrículas son ficticios. No corre con DEBUG apagado salvo --forzar.
"""

from datetime import time, timedelta

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import Barrio, City
from apps.directorio.models import Consentimiento, FranjaGuardia, Oficio, Rubro
from apps.resenas.models import Resena

D = FranjaGuardia.Dia
FINDE = [(D.SABADO, time(8), time(22)), (D.DOMINGO, time(8), time(22))]
NOCHE_VIERNES = [(D.VIERNES, time(20), time(8))]

# nombre, rubros, barrios (None = toda la ciudad), años, garantía, matrícula,
# franjas de guardia, descripción, reseñas [(autor, estrellas, texto)]
OFICIOS = [
    ("Ramón Acosta", ["Plomería", "Destapaciones"], None, 22, "6 meses en mano de obra", "", FINDE,
     "Plomería en general, termotanques y destapaciones. Trabajo con factura.",
     [("Marcela, B° Belgrano", 5, "Vino un domingo a la mañana por una pérdida debajo de la pileta. Rápido y prolijo."),
      ("Daniel, Centro", 5, "Cambió toda la cañería del baño. Cumplió con el presupuesto."),
      ("Sofía, B° Evita", 4, "Buen trabajo, llegó un poco más tarde de lo acordado.")]),
    ("Hugo Bustos", ["Plomería"], ["Centro", "Belgrano", "Jardín", "Fátima"], 9, "3 meses", "", NOCHE_VIERNES,
     "Reparaciones rápidas: canillas, mochilas, flexibles, pérdidas.",
     [("Ana, B° Jardín", 5, "Me salvó un viernes a la noche con el agua corriendo.")]),
    ("Gabriela Cárdenas", ["Gas", "Plomería"], None, 15, "1 año en instalaciones nuevas", "MG-4471", FINDE,
     "Gasista matriculada. Instalaciones, pruebas de hermeticidad, calefactores y calderas.",
     [("Jorge, B° Del Carmen", 5, "Revisó todos los artefactos antes del invierno. Muy profesional."),
      ("Lucía, B° San Benito", 5, "Explicó todo y dejó el certificado en regla.")]),
    ("Néstor Díaz", ["Gas"], ["Puerto", "Centro", "Náutico"], 30, "6 meses", "MG-1203", NOCHE_VIERNES + FINDE,
     "Más de 30 años en gas. Estufas, calefactores, termotanques.",
     [("Rosa, B° Puerto", 4, "Sabe muchísimo. Hay que insistirle para que conteste.")]),
    ("Electricidad Ferreyra", ["Electricidad"], None, 12, "6 meses", "ME-0832", FINDE,
     "Instalaciones domiciliarias y comerciales, tableros, puesta a tierra.",
     [("Martín, B° Bicentenario", 5, "Rehízo el tablero y ahora no salta más la térmica."),
      ("Carla, Centro", 4, "Bien, aunque el presupuesto tardó un par de días.")]),
    ("Sergio Gómez", ["Electricidad"], ["Evita", "Gaucho Rivero", "Ceferino Namuncurá", "Los Lolos", "Madres a la Lucha"], 6, "", "ME-1590", NOCHE_VIERNES,
     "Electricista matriculado. Urgencias y reparaciones.", []),
    ("Cerrajería La Llave", ["Cerrajería"], None, 18, "3 meses en cerraduras colocadas", "", NOCHE_VIERNES + FINDE,
     "Aperturas, cambio de combinación, cerraduras de seguridad.",
     [("Valeria, B° Náutico", 5, "Me quedé afuera un sábado a las 23 y en media hora estaba adentro."),
      ("Pablo, Centro", 5, "Precio justo, lo que dijo por teléfono.")]),
    ("Julio Herrera", ["Cerrajería", "Herrería"], ["Centro", "Belgrano", "Del Carmen"], 25, "", "", [],
     "Cerrajería y herrería: rejas, portones, cerraduras.", []),
    ("Mirta Ibáñez", ["Pintura"], None, 14, "1 año contra descascarado", "", [],
     "Pintura interior y exterior, impermeabilización de techos.",
     [("Ricardo, B° APAP", 5, "Pintó toda la casa en una semana, dejó todo limpio.")]),
    ("Techos del Sur", ["Techos y zinguería", "Albañilería"], None, 20, "2 años en chapa nueva", "", [],
     "Techos de chapa, canaletas, reparación de daños por viento.",
     [("Inés, B° 22 de Septiembre", 4, "Vinieron después del temporal y aseguraron todo el techo."),
      ("Omar, B° Bicentenario", 5, "Muy buenos, recomiendo.")]),
    ("Pedro Juárez", ["Albañilería", "Durlock y construcción en seco"], ["San Benito", "Bicentenario", "Evita"], 10, "", "", [],
     "Ampliaciones, revoques, contrapisos y durlock.", []),
    ("Carpintería Lagos", ["Carpintería"], None, 35, "6 meses", "", [],
     "Muebles a medida, placares, aberturas de madera.",
     [("Graciela, Centro", 5, "Hizo un placard a medida impecable.")]),
    ("Destapaciones Patagonia", ["Destapaciones"], None, 8, "", "", [],
     "Destapaciones con máquina, cámaras sépticas.", []),
    ("Refrigeración Molina", ["Refrigeración y aire acondicionado"], None, 11, "3 meses", "", [],
     "Service de heladeras y aire acondicionado.", []),
]

# Uno pendiente (no debe verse) y uno suspendido (no figura en listados).
EXTRA = [
    ("Electricista Pendiente", ["Electricidad"], Oficio.Estado.PENDIENTE, ""),
    ("Plomero Suspendido", ["Plomería"], Oficio.Estado.SUSPENDIDO, "Demo: no se presentó a dos trabajos aceptados."),
]

TEXTO_CONSENTIMIENTO = "Acepto que mi teléfono se muestre en la lista de urgencias cuando ningún oficio de guardia acepte el pedido."


def nombre_guia(nombre):
    """Personas: "Ramón Acosta" → "Acosta, Ramón". Comercios: vacío."""
    comercios = ("Electricidad", "Cerrajería", "Techos", "Carpintería", "Destapaciones", "Refrigeración")
    if nombre.startswith(comercios):
        return ""
    nombre_pila, _, apellido = nombre.rpartition(" ")
    return f"{apellido}, {nombre_pila}"


class Command(BaseCommand):
    help = "Carga oficios y reseñas de ejemplo (solo desarrollo)."

    def add_arguments(self, parser):
        parser.add_argument("--forzar", action="store_true", help="Correr aunque DEBUG esté apagado.")

    @transaction.atomic
    def handle(self, *args, forzar=False, **options):
        if not settings.DEBUG and not forzar:
            raise CommandError("Esto carga datos inventados. Con DEBUG apagado, usá --forzar.")
        call_command("cargar_base", stdout=self.stdout)

        city = City.objects.get(slug="rio-gallegos")
        rubros = {r.nombre: r for r in Rubro.objects.all()}
        barrios = {b.nombre: b for b in Barrio.objects.filter(city=city)}
        ahora = timezone.now()

        for i, (nombre, rs, bs, anios, garantia, matricula, franjas, desc, resenas) in enumerate(OFICIOS):
            o, _ = Oficio.objects.update_or_create(
                city=city, slug=slugify(nombre),
                defaults=dict(
                    nombre=nombre, nombre_guia=nombre_guia(nombre), descripcion=desc, anios_oficio=anios, garantia=garantia,
                    matricula=matricula, cubre_toda_la_ciudad=bs is None,
                    telefono=f"2966 15-000-{i:03d}", email=f"demo{i}@example.com",
                    estado=Oficio.Estado.APROBADO, aprobado_at=ahora,
                    dni_verificado_at=ahora, de_guardia=bool(franjas),
                ),
            )
            o.rubros.set([rubros[r] for r in rs])
            o.barrios.set([barrios[b] for b in bs or []])
            o.franjas.all().delete()
            for dia, desde, hasta in franjas:
                o.franjas.create(dia=dia, desde=desde, hasta=hasta)
            if franjas and not o.telefono_en_fallback:
                Consentimiento.registrar(o, Consentimiento.Tipo.TELEFONO_FALLBACK, True, TEXTO_CONSENTIMIENTO)
            o.resenas.filter(bootstrap=True).delete()
            for j, (autor, estrellas, texto) in enumerate(resenas):
                r = Resena.objects.create(oficio=o, bootstrap=True, autor=autor, estrellas=estrellas, texto=texto)
                # Fechas escalonadas para que no parezcan cargadas el mismo día.
                Resena.objects.filter(pk=r.pk).update(creado_at=ahora - timedelta(days=20 + 35 * j + 7 * i))

        for nombre, rs, estado, motivo in EXTRA:
            o, _ = Oficio.objects.update_or_create(
                city=city, slug=slugify(nombre),
                defaults=dict(nombre=nombre, telefono="2966 15-000-999", email="demo-extra@example.com",
                              estado=estado, motivo_suspension=motivo, cubre_toda_la_ciudad=True),
            )
            o.rubros.set([rubros[r] for r in rs])

        self.stdout.write(self.style.SUCCESS(
            f"Demo: {Oficio.objects.publicados().count()} oficios publicados, "
            f"{Resena.objects.count()} reseñas."
        ))
