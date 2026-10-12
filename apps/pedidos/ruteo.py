"""
Lógica de pedidos: a quién se deriva, escalado de urgencias y transiciones
de estado. Todo lo que cambia estado corre en una transacción (IMMEDIATE en
SQLite, así web y worker se serializan) y vuelve a leer el pedido adentro:
si dos procesos compiten, el segundo ve el estado nuevo y no hace nada.
"""

import unicodedata
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.directorio.models import Oficio

from . import notificaciones as notif
from .models import Derivacion, Pedido

# Limpieza: pedidos que ya no van a moverse.
DIAS_PRESUPUESTO_ABIERTO = 30
HORAS_URGENCIA_EN_FALLBACK = 24


def _norm(texto):
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def detectar_rubro(texto, rubros):
    """Texto libre → rubro, por palabras clave. Gana el que más texto matchea
    (así "olor a gas" le gana a "olor"). None si no hay match."""
    t = _norm(texto)
    mejor, puntaje_mejor = None, 0
    for rubro in rubros:
        puntaje = sum(len(p) for p in map(_norm, rubro.lista_palabras_clave()) if p and p in t)
        if puntaje > puntaje_mejor:
            mejor, puntaje_mejor = rubro, puntaje
    return mejor


def _cubre(oficio, barrio_id):
    return oficio.cubre_toda_la_ciudad or any(b.id == barrio_id for b in oficio.barrios.all())


# --- Presupuesto: fan-out ------------------------------------------------------


def candidatos_presupuesto(pedido, ahora=None):
    ahora = ahora or timezone.now()
    qs = (
        Oficio.objects.publicados()
        .filter(city=pedido.city, rubros=pedido.rubro)
        .filter(Q(cubre_toda_la_ciudad=True) | Q(barrios=pedido.barrio))
        .distinct()
    )
    return [o for o in qs if o.disponible(ahora)]


@transaction.atomic
def derivar_presupuesto(pedido, oficio_directo=None):
    """Ofrece el pedido a todos los oficios de la zona, o solo al elegido
    desde su ficha. Devuelve la cantidad de derivaciones."""
    oficios = [oficio_directo] if oficio_directo else candidatos_presupuesto(pedido)
    for o in oficios:
        d = Derivacion.objects.create(pedido=pedido, oficio=o)
        notif.avisar_oficio(d, "oficio_presupuesto")
    notif.avisar_vecino(pedido, "vecino_recibido", cantidad=len(oficios))
    if not oficios:
        notif.avisar_admin("admin_sin_oficios", pedido)
    return len(oficios)


@transaction.atomic
def responder_presupuesto(derivacion, mensaje, monto=None):
    d = Derivacion.objects.select_for_update().select_related("pedido").get(pk=derivacion.pk)
    if d.pedido.estado != Pedido.Estado.ABIERTO or d.estado != Derivacion.Estado.ENVIADA:
        return False
    d.estado = Derivacion.Estado.RESPONDIDA
    d.mensaje, d.monto, d.respondido_at = mensaje, monto, timezone.now()
    d.save(update_fields=["estado", "mensaje", "monto", "respondido_at"])
    notif.avisar_vecino(d.pedido, "vecino_respuesta", derivacion=d)
    return True


@transaction.atomic
def elegir(pedido, derivacion_id):
    pedido = Pedido.objects.get(pk=pedido.pk)
    d = pedido.derivaciones.select_related("oficio").filter(pk=derivacion_id).first()
    if pedido.estado != Pedido.Estado.ABIERTO or not d or d.estado != Derivacion.Estado.RESPONDIDA:
        return False
    _asignar(pedido, d)
    notif.avisar_oficio(d, "oficio_elegido")
    return True


# --- Urgencia: escalado uno por uno --------------------------------------------


def cola_urgencia(pedido, ahora=None):
    """Oficios de guardia ahora, que todavía no recibieron este pedido.
    Primero los que cubren el barrio; dentro de cada grupo, el que recibió
    un trabajo hace más tiempo (nunca = primero)."""
    ahora = ahora or timezone.now()
    ya = pedido.derivaciones.values_list("oficio_id", flat=True)
    base = (
        Oficio.objects.publicados()
        .filter(city=pedido.city, rubros=pedido.rubro, de_guardia=True)
        .exclude(pk__in=ya)
        .select_related("city")
        .prefetch_related("franjas", "barrios")
    )
    de_turno = [o for o in base if o.de_guardia_en(ahora)]

    def orden(o):
        return (o.ultima_asignacion is not None, o.ultima_asignacion or ahora, o.pk)

    en_zona = sorted((o for o in de_turno if _cubre(o, pedido.barrio_id)), key=orden)
    fuera = sorted((o for o in de_turno if not _cubre(o, pedido.barrio_id)), key=orden)
    return en_zona + fuera


@transaction.atomic
def escalar(pedido_id, ahora=None):
    """Idempotente: si hay un oficio con el plazo corriendo, no hace nada; si
    venció, pasa al siguiente; si no queda nadie, fallback. Lo llaman la web
    (al crear y al rechazar) y el worker (en cada vuelta)."""
    ahora = ahora or timezone.now()
    pedido = Pedido.objects.select_related("city", "rubro", "barrio").get(pk=pedido_id)
    if not pedido.es_urgencia or pedido.estado != Pedido.Estado.ABIERTO or pedido.fallback_at:
        return None
    enviadas = pedido.derivaciones.filter(estado=Derivacion.Estado.ENVIADA)
    if enviadas.filter(vence_at__gt=ahora).exists():
        return None  # alguien tiene el pedido en la mano
    enviadas.update(estado=Derivacion.Estado.VENCIDA)

    cola = cola_urgencia(pedido, ahora)
    if not cola:
        pedido.fallback_at = ahora
        pedido.save(update_fields=["fallback_at", "actualizado_at"])
        notif.avisar_admin("admin_fallback", pedido)
        notif.avisar_vecino(pedido, "vecino_fallback")
        return None

    d = Derivacion.objects.create(
        pedido=pedido,
        oficio=cola[0],
        posicion=pedido.derivaciones.count() + 1,
        vence_at=ahora + timedelta(minutes=settings.URGENCIA_MINUTOS_PARA_ACEPTAR),
    )
    notif.avisar_oficio(d, "oficio_urgencia")
    return d


@transaction.atomic
def aceptar_urgencia(derivacion):
    """Vale aunque se le haya vencido el plazo, mientras nadie más lo haya
    tomado: al vecino le sirve más que esperar al siguiente."""
    d = Derivacion.objects.select_related("pedido", "oficio").get(pk=derivacion.pk)
    pedido = d.pedido
    if pedido.estado != Pedido.Estado.ABIERTO or d.estado not in (
        Derivacion.Estado.ENVIADA,
        Derivacion.Estado.VENCIDA,
    ):
        return False
    _asignar(pedido, d)
    # Si había otro oficio con el aviso en la mano, su link ya muestra que
    # lo tomó otro; lo marcamos vencido para el registro.
    pedido.derivaciones.filter(estado=Derivacion.Estado.ENVIADA).update(estado=Derivacion.Estado.VENCIDA)
    notif.avisar_vecino(pedido, "vecino_aceptado", oficio=d.oficio)
    return True


@transaction.atomic
def rechazar(derivacion):
    d = Derivacion.objects.select_related("pedido").get(pk=derivacion.pk)
    if d.pedido.estado != Pedido.Estado.ABIERTO or d.estado not in (
        Derivacion.Estado.ENVIADA,
        Derivacion.Estado.VENCIDA,
    ):
        return False
    d.estado = Derivacion.Estado.RECHAZADA
    d.respondido_at = timezone.now()
    d.save(update_fields=["estado", "respondido_at"])
    if d.pedido.es_urgencia:
        escalar(d.pedido_id)  # no esperamos al vencimiento
    return True


# --- Transiciones comunes ------------------------------------------------------


def _asignar(pedido, derivacion):
    ahora = timezone.now()
    derivacion.estado = Derivacion.Estado.ELEGIDA
    derivacion.respondido_at = derivacion.respondido_at or ahora
    derivacion.save(update_fields=["estado", "respondido_at"])
    pedido.estado = Pedido.Estado.ASIGNADO
    pedido.oficio_asignado = derivacion.oficio
    pedido.asignado_at = ahora
    pedido.save(update_fields=["estado", "oficio_asignado", "asignado_at", "actualizado_at"])
    Oficio.objects.filter(pk=derivacion.oficio_id).update(ultima_asignacion=ahora)


@transaction.atomic
def confirmar_trabajo(pedido):
    pedido = Pedido.objects.get(pk=pedido.pk)
    if pedido.estado != Pedido.Estado.ASIGNADO:
        return False
    pedido.estado = Pedido.Estado.COMPLETADO
    pedido.completado_at = timezone.now()
    pedido.completado_por = Pedido.Completado.VECINO
    pedido.save(update_fields=["estado", "completado_at", "completado_por", "actualizado_at"])
    return True


@transaction.atomic
def cancelar(pedido):
    pedido = Pedido.objects.get(pk=pedido.pk)
    if pedido.estado != Pedido.Estado.ABIERTO:
        return False
    _cerrar(pedido, Pedido.MotivoCierre.CANCELADO)
    return True


def _cerrar(pedido, motivo):
    pedido.estado = Pedido.Estado.CERRADO
    pedido.motivo_cierre = motivo
    pedido.cerrado_at = timezone.now()
    pedido.save(update_fields=["estado", "motivo_cierre", "cerrado_at", "actualizado_at"])


# --- Tareas del worker ---------------------------------------------------------


def procesar_urgencias(ahora=None):
    ids = Pedido.objects.filter(
        es_urgencia=True, estado=Pedido.Estado.ABIERTO, fallback_at__isnull=True
    ).values_list("pk", flat=True)
    return sum(1 for pk in list(ids) if escalar(pk, ahora))


def autocompletar(ahora=None):
    """Asignados hace más de PEDIDO_HORAS_AUTOCOMPLETAR sin confirmación del
    vecino → completados (y se le pide la reseña)."""
    ahora = ahora or timezone.now()
    limite = ahora - timedelta(hours=settings.PEDIDO_HORAS_AUTOCOMPLETAR)
    n = 0
    for pk in list(
        Pedido.objects.filter(estado=Pedido.Estado.ASIGNADO, asignado_at__lte=limite).values_list("pk", flat=True)
    ):
        with transaction.atomic():
            pedido = Pedido.objects.get(pk=pk)
            if pedido.estado != Pedido.Estado.ASIGNADO:
                continue
            pedido.estado = Pedido.Estado.COMPLETADO
            pedido.completado_at = ahora
            pedido.completado_por = Pedido.Completado.AUTO
            pedido.save(update_fields=["estado", "completado_at", "completado_por", "actualizado_at"])
            notif.avisar_vecino(pedido, "vecino_pedir_resena")
            n += 1
    return n


def cerrar_viejos(ahora=None):
    ahora = ahora or timezone.now()
    viejos = Pedido.objects.filter(estado=Pedido.Estado.ABIERTO).filter(
        Q(es_urgencia=False, creado_at__lte=ahora - timedelta(days=DIAS_PRESUPUESTO_ABIERTO))
        | Q(es_urgencia=True, fallback_at__lte=ahora - timedelta(hours=HORAS_URGENCIA_EN_FALLBACK))
    )
    n = 0
    for pedido in viejos:
        with transaction.atomic():
            _cerrar(pedido, Pedido.MotivoCierre.SIN_RESPUESTA)
            n += 1
    return n
