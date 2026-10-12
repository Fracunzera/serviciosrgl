"""Minimización de datos (Ley 25.326): lo que ya no hace falta, se borra."""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Mensaje, Pedido


def anonimizar_viejos(ahora=None):
    """Pedidos terminados hace más de PEDIDOS_ANONIMIZAR_DIAS: se borran los
    datos del vecino y las fotos. Quedan rubro, barrio, fechas, el oficio
    asignado y la reseña (que el vecino firmó como quiso)."""
    ahora = ahora or timezone.now()
    limite = ahora - timedelta(days=settings.PEDIDOS_ANONIMIZAR_DIAS)
    viejos = Pedido.objects.filter(anonimizado_at__isnull=True).filter(
        Q(estado=Pedido.Estado.COMPLETADO, completado_at__lte=limite)
        | Q(estado=Pedido.Estado.CERRADO, cerrado_at__lte=limite)
    )
    n = 0
    for pedido in viejos:
        fotos = list(pedido.fotos.all())
        with transaction.atomic():
            Pedido.objects.filter(pk=pedido.pk).update(
                nombre_vecino="(anónimo)", telefono_vecino="", email_vecino="", direccion="",
                descripcion="", anonimizado_at=ahora,
            )
            pedido.fotos.all().delete()
            # Los mails de este pedido tienen nombre, teléfono y dirección.
            pedido.mensajes.all().delete()
            n += 1
        for f in fotos:
            f.imagen.delete(save=False)
    # Bandeja: los mails ya enviados (o fallidos) no sirven pasado el plazo.
    Mensaje.objects.filter(creado_at__lte=limite).exclude(estado=Mensaje.Estado.PENDIENTE).delete()
    return n
