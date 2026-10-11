from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.core.views import get_city, sin_acentos
from apps.directorio.models import Oficio, Rubro


def rubro(request, city_slug, slug):
    city = get_city(city_slug)
    rubro = get_object_or_404(Rubro, slug=slug)
    # Orden alfabético, como la guía: nadie compra el primer lugar.
    oficios = sorted(
        Oficio.objects.publicados()
        .filter(city=city, rubros=rubro)
        .con_rating()
        .prefetch_related("rubros", "barrios"),
        key=lambda o: sin_acentos(o.nombre_guia or o.nombre),
    )
    # "Página" de la guía: posición del rubro en el índice alfabético.
    orden = sorted(Rubro.objects.values_list("id", "nombre"), key=lambda r: sin_acentos(r[1]))
    pagina = [r[0] for r in orden].index(rubro.id) + 1
    return render(request, "directorio/rubro.html", {
        "city": city,
        "rubro": rubro,
        "oficios": oficios,
        "ver_tambien": rubro.ver_tambien.all(),
        "pagina": pagina,
    })


def oficio(request, city_slug, slug):
    city = get_city(city_slug)
    oficio = get_object_or_404(
        Oficio.objects.con_rating().prefetch_related("rubros", "barrios", "fotos"),
        city=city, slug=slug,
    )
    # Pendiente: no existe para el público. Suspendido: la ficha sigue
    # accesible por link directo (las reseñas quedan publicadas), pero no
    # figura en el directorio ni recibe pedidos.
    if oficio.estado == Oficio.Estado.PENDIENTE:
        raise Http404
    return render(request, "directorio/oficio.html", {
        "city": city,
        "oficio": oficio,
        "resenas": oficio.resenas.filter(estado="publicada").select_related("pedido__barrio"),
        "suspendido": oficio.estado == Oficio.Estado.SUSPENDIDO,
    })
