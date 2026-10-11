"""
Datos base reales (sirven en producción): Río Gallegos, sus barrios y los
rubros. Idempotente: se puede correr de nuevo sin duplicar; no pisa lo que
se haya editado en el admin salvo los campos listados acá.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.core.models import Barrio, City
from apps.directorio.models import Rubro

# Lista inicial: revisarla y completarla desde el admin.
BARRIOS = [
    "Centro", "Belgrano", "Evita", "Del Carmen", "San Benito", "Bicentenario",
    "Jardín", "Gaucho Rivero", "Náutico", "Ceferino Namuncurá", "Los Lolos",
    "Madres a la Lucha", "Puerto", "Fátima", "APAP", "22 de Septiembre",
]

# nombre, requiere_matricula, en_urgencias, palabras clave
RUBROS = [
    ("Plomería", False, True, "pérdida, pierde, caño, cañería, canilla, agua, inodoro, mochila, inundación, inundado, bomba, tanque, se rompió el caño"),
    ("Gas", True, True, "gas, olor a gas, estufa, calefactor, caldera, garrafa, cocina, horno, termotanque, piloto"),
    ("Electricidad", True, True, "luz, sin luz, corte, cable, chispa, enchufe, tomacorriente, térmica, disyuntor, cortocircuito, tablero, olor a quemado"),
    ("Cerrajería", False, True, "llave, llaves, cerradura, puerta, trabada, me quedé afuera, encerrado, candado, cerrojo"),
    ("Destapaciones", False, False, "tapado, destapar, cloaca, desagüe, pileta, rejilla, cámara séptica, pozo"),
    ("Albañilería", False, False, "pared, revoque, contrapiso, ladrillo, humedad, grieta, cerámica"),
    ("Techos y zinguería", False, False, "techo, chapa, chapas, gotera, canaleta, zinguería, viento"),
    ("Pintura", False, False, "pintar, pintura, látex, impermeabilizante"),
    ("Herrería", False, False, "reja, portón, soldadura, herrería"),
    ("Carpintería", False, False, "madera, mueble, placard, puerta de madera, aberturas"),
    ("Durlock y construcción en seco", False, False, "durlock, cielorraso, tabique, placa"),
    ("Refrigeración y aire acondicionado", False, False, "heladera, freezer, aire acondicionado, split"),
    ("Vidriería", False, False, "vidrio, ventana, espejo, vidrio roto"),
]

VER_TAMBIEN = [
    ("Plomería", "Destapaciones"),
    ("Plomería", "Gas"),
    ("Electricidad", "Refrigeración y aire acondicionado"),
    ("Cerrajería", "Herrería"),
    ("Techos y zinguería", "Albañilería"),
    ("Durlock y construcción en seco", "Albañilería"),
    ("Pintura", "Albañilería"),
    ("Carpintería", "Vidriería"),
]


class Command(BaseCommand):
    help = "Carga Río Gallegos, sus barrios y los rubros (idempotente)."

    @transaction.atomic
    def handle(self, *args, **options):
        city, _ = City.objects.get_or_create(
            slug="rio-gallegos",
            defaults={"nombre": "Río Gallegos", "timezone": "America/Argentina/Rio_Gallegos"},
        )
        for nombre in BARRIOS:
            Barrio.objects.get_or_create(city=city, slug=slugify(nombre), defaults={"nombre": nombre})

        rubros = {}
        for i, (nombre, matricula, urgencias, claves) in enumerate(RUBROS):
            rubros[nombre], _ = Rubro.objects.update_or_create(
                slug=slugify(nombre),
                defaults={
                    "nombre": nombre,
                    "requiere_matricula": matricula,
                    "en_urgencias": urgencias,
                    "palabras_clave": claves,
                    "orden": i,
                },
            )
        for a, b in VER_TAMBIEN:
            rubros[a].ver_tambien.add(rubros[b])

        self.stdout.write(self.style.SUCCESS(
            f"{city}: {city.barrios.count()} barrios, {Rubro.objects.count()} rubros."
        ))
