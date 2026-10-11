import unicodedata
from itertools import groupby

from django.conf import settings
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.models import City
from apps.directorio.models import Oficio, Rubro


def get_city(slug):
    return get_object_or_404(City, slug=slug, activa=True)


def sin_acentos(texto):
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().upper()


def raiz(request):
    return redirect("core:home", city_slug=settings.DEFAULT_CITY_SLUG)


def home(request, city_slug):
    city = get_city(city_slug)
    rubros = sorted(
        Rubro.objects.annotate(
            cantidad=Count(
                "oficios",
                filter=Q(oficios__city=city, oficios__estado=Oficio.Estado.APROBADO),
                distinct=True,
            )
        ),
        key=lambda r: sin_acentos(r.nombre),
    )
    # Índice alfabético como el de la guía: letra → rubros.
    indice = [(letra, list(grupo)) for letra, grupo in groupby(rubros, key=lambda r: sin_acentos(r.nombre)[0])]
    return render(request, "core/home.html", {
        "city": city,
        "indice": indice,
        "urgencias": [r for r in rubros if r.en_urgencias],
    })
