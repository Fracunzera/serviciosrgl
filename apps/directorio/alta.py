from django.conf import settings
from django.db import transaction
from django.urls import reverse

from apps.pedidos import notificaciones

from .forms import FRANJAS, TEXTO_FALLBACK, TEXTO_TERMINOS
from .models import Consentimiento, FotoTrabajo, Oficio


@transaction.atomic
def registrar_alta(form):
    """Alta desde la web: queda pendiente hasta que el admin verifique el DNI."""
    datos = form.cleaned_data
    oficio = form.save(commit=False)
    oficio.estado = Oficio.Estado.PENDIENTE
    oficio.de_guardia = bool(datos["franjas"])
    oficio.save()
    form.save_m2m()
    for clave in datos["franjas"]:
        for dia, desde, hasta in FRANJAS[clave][1]:
            oficio.franjas.create(dia=dia, desde=desde, hasta=hasta)
    for i, foto in enumerate(datos.get("fotos") or []):
        FotoTrabajo.objects.create(oficio=oficio, imagen=foto, orden=i)
    Consentimiento.registrar(oficio, Consentimiento.Tipo.TERMINOS, True, TEXTO_TERMINOS)
    if datos.get("consiento_fallback"):
        Consentimiento.registrar(oficio, Consentimiento.Tipo.TELEFONO_FALLBACK, True, TEXTO_FALLBACK)

    notificaciones.avisar_oficio_general(oficio, "oficio_solicitud")
    notificaciones.avisar_admin(
        "admin_nuevo_oficio", oficio=oficio,
        link_admin=settings.SITE_URL + reverse("admin:directorio_oficio_change", args=[oficio.pk]),
    )
    return oficio
