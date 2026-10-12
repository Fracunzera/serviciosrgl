"""
Cambios de estado de un oficio. Las usan las acciones del admin; viven acá
para poder testearlas sin el admin y para que las reglas estén en un solo lugar.
"""

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Oficio


class NoSePuede(Exception):
    pass


@transaction.atomic
def aprobar(oficio):
    """Pendiente o suspendido → aprobado. Aprobar implica que el admin
    verificó el DNI (si no estaba marcado, se marca ahora)."""
    from apps.pedidos import notificaciones

    if oficio.falta_matricula():
        rubros = ", ".join(r.nombre for r in oficio.rubros.filter(requiere_matricula=True))
        raise NoSePuede(f"{oficio}: falta la matrícula ({rubros}).")
    primera_vez = oficio.aprobado_at is None
    ahora = timezone.now()
    oficio.estado = Oficio.Estado.APROBADO
    oficio.aprobado_at = oficio.aprobado_at or ahora
    oficio.dni_verificado_at = oficio.dni_verificado_at or ahora
    oficio.save()
    notificaciones.avisar_oficio_general(
        oficio, "oficio_bienvenida" if primera_vez else "oficio_reactivado"
    )


@transaction.atomic
def suspender(oficio, motivo):
    """Sale del directorio y del ruteo; las reseñas quedan. Revoca sus links
    y libera lo que tenía en la mano para que nadie quede esperándolo."""
    from apps.pedidos.models import Derivacion, Pedido

    motivo = (motivo or "").strip()
    if not motivo:
        raise NoSePuede("Para suspender hay que dejar asentado el motivo.")
    oficio.estado = Oficio.Estado.SUSPENDIDO
    oficio.suspendido_at = timezone.now()
    oficio.motivo_suspension = motivo
    oficio.version_links = F("version_links") + 1
    oficio.save()
    oficio.refresh_from_db()

    pendientes = Derivacion.objects.filter(
        oficio=oficio, estado=Derivacion.Estado.ENVIADA, pedido__estado=Pedido.Estado.ABIERTO
    )
    # Urgencia: vencida ya → el worker escala en la próxima vuelta.
    pendientes.filter(pedido__es_urgencia=True).update(estado=Derivacion.Estado.VENCIDA)
    pendientes.filter(pedido__es_urgencia=False).update(estado=Derivacion.Estado.RECHAZADA)


def revocar_links(oficio):
    Oficio.objects.filter(pk=oficio.pk).update(version_links=F("version_links") + 1)
    oficio.refresh_from_db()


def enviar_link_disponibilidad(oficio):
    from apps.pedidos import notificaciones

    notificaciones.avisar_oficio_general(oficio, "oficio_disponibilidad")
