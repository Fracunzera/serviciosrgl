"""
Links firmados para oficios (sin login). Llevan el id y Oficio.version_links:
subir la versión invalida de golpe todos los links vigentes de ese oficio.
"""

from django.conf import settings
from django.core import signing
from django.urls import reverse

from apps.directorio.models import Oficio

from .models import Derivacion

SAL_DERIVACION = "pedidos.derivacion"
SAL_OFICIO = "pedidos.oficio"
MAX_EDAD = 60 * 60 * 24 * settings.LINKS_OFICIO_DIAS


def absoluta(path):
    return settings.SITE_URL + path


def link_derivacion(derivacion):
    firma = signing.dumps({"d": derivacion.pk, "v": derivacion.oficio.version_links}, salt=SAL_DERIVACION)
    return absoluta(reverse("pedidos:derivacion", args=[firma]))


def link_disponibilidad(oficio):
    firma = signing.dumps({"o": oficio.pk, "v": oficio.version_links}, salt=SAL_OFICIO)
    return absoluta(reverse("pedidos:disponibilidad", args=[firma]))


def leer_derivacion(firma):
    """Derivacion o None si la firma es inválida, vieja o de una versión revocada."""
    try:
        datos = signing.loads(firma, salt=SAL_DERIVACION, max_age=MAX_EDAD)
    except signing.BadSignature:
        return None
    d = (
        Derivacion.objects.select_related("oficio", "pedido__rubro", "pedido__barrio", "pedido__city")
        .filter(pk=datos.get("d"))
        .first()
    )
    if d is None or d.oficio.version_links != datos.get("v"):
        return None
    return d


def leer_oficio(firma):
    try:
        datos = signing.loads(firma, salt=SAL_OFICIO, max_age=MAX_EDAD)
    except signing.BadSignature:
        return None
    o = Oficio.objects.filter(pk=datos.get("o")).first()
    if o is None or o.version_links != datos.get("v"):
        return None
    return o
