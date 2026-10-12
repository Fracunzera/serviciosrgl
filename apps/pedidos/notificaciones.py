"""
Notificador. Dos mitades:

1. Encolar (web y worker): arman el texto y crean filas Mensaje. Corren
   dentro de la transacción del cambio de estado: si el cambio se revierte,
   el aviso también.
2. Enviar (solo worker): `enviar_pendientes()` despacha la bandeja por
   canal, con reintentos.

Qué avisos le llegan a un oficio lo decide settings.NOTIFICADORES:
- "email": el aviso va al mail del oficio.
- "semimanual": copia al admin con un link wa.me listo para reenviarle.
Sumar Telegram = un sender "telegram" + una política que encole a
oficio.telegram_chat_id. El ruteo no cambia.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from .links import link_derivacion, link_disponibilidad
from .models import Mensaje
from .whatsapp import link_wa

log = logging.getLogger(__name__)

MAX_INTENTOS = 6


def _render(plantilla, contexto):
    """Primera línea del template = asunto; el resto = cuerpo."""
    texto = render_to_string(f"pedidos/mails/{plantilla}.txt", contexto).strip()
    asunto, _, cuerpo = texto.partition("\n")
    return asunto.strip(), cuerpo.strip() + "\n"


def _encolar(destino, asunto, texto, canal="email", **refs):
    return Mensaje.objects.create(canal=canal, destino=destino, asunto=asunto, texto=texto, **refs)


def avisar_oficio(derivacion, plantilla):
    oficio, pedido = derivacion.oficio, derivacion.pedido
    contexto = {
        "oficio": oficio,
        "pedido": pedido,
        "derivacion": derivacion,
        "link": link_derivacion(derivacion),
        "link_disponibilidad": link_disponibilidad(oficio),
        "minutos": settings.URGENCIA_MINUTOS_PARA_ACEPTAR,
    }
    asunto, texto = _render(plantilla, contexto)
    canales = []
    if "email" in settings.NOTIFICADORES and oficio.email:
        _encolar(oficio.email, asunto, texto, derivacion=derivacion, pedido=pedido)
        canales.append("email")
    if "semimanual" in settings.NOTIFICADORES and settings.ADMIN_EMAIL:
        wa = link_wa(oficio.telefono, f"{asunto}\n\n{texto}", pedido.city.codigo_area)
        _, texto_admin = _render("admin_reenviar", {**contexto, "asunto": asunto, "texto": texto, "wa": wa})
        _encolar(settings.ADMIN_EMAIL, f"[Reenviar a {oficio.nombre}] {asunto}", texto_admin,
                 derivacion=derivacion, pedido=pedido)
        canales.append("semimanual")
    derivacion.canales = ",".join(canales)
    derivacion.save(update_fields=["canales"])


def avisar_vecino(pedido, plantilla, **extra):
    if not pedido.email_vecino:
        return
    asunto, texto = _render(plantilla, {"pedido": pedido, "link": link_seguimiento(pedido), **extra})
    _encolar(pedido.email_vecino, asunto, texto, pedido=pedido)


def avisar_admin(plantilla, pedido=None, **extra):
    if not settings.ADMIN_EMAIL:
        log.warning("Aviso al admin sin ADMIN_EMAIL configurado: %s", plantilla)
        return
    contexto = {"pedido": pedido, **extra}
    if pedido:
        contexto["link"] = link_seguimiento(pedido)
    asunto, texto = _render(plantilla, contexto)
    _encolar(settings.ADMIN_EMAIL, asunto, texto, pedido=pedido)


def link_seguimiento(pedido):
    from django.urls import reverse

    return settings.SITE_URL + reverse("pedidos:seguimiento", args=[pedido.token])


# --- Envío (worker) ----------------------------------------------------------


def _enviar_email(m):
    send_mail(m.asunto, m.texto, settings.DEFAULT_FROM_EMAIL, [m.destino])


SENDERS = {"email": _enviar_email}


def enviar_pendientes(limite=50):
    """Despacha la bandeja. Cada envío actualiza su fila por separado (fuera
    de una transacción larga): un SMTP lento no bloquea la base."""
    ahora = timezone.now()
    pendientes = list(
        Mensaje.objects.filter(estado=Mensaje.Estado.PENDIENTE, proximo_intento_at__lte=ahora)
        .order_by("creado_at")[:limite]
    )
    enviados = 0
    for m in pendientes:
        sender = SENDERS.get(m.canal)
        try:
            if sender is None:
                raise RuntimeError(f"Canal sin sender: {m.canal}")
            sender(m)
        except Exception as e:  # noqa: BLE001 — cualquier falla se reintenta
            m.intentos += 1
            m.ultimo_error = f"{type(e).__name__}: {e}"
            if m.intentos >= MAX_INTENTOS:
                m.estado = Mensaje.Estado.FALLIDO
                log.error("Mensaje %s falló definitivamente: %s", m.pk, m.ultimo_error)
            else:
                # 2, 4, 8, 16, 32 minutos.
                m.proximo_intento_at = timezone.now() + timedelta(minutes=2**m.intentos)
            m.save(update_fields=["intentos", "ultimo_error", "estado", "proximo_intento_at"])
            continue
        m.estado = Mensaje.Estado.ENVIADO
        m.enviado_at = timezone.now()
        m.save(update_fields=["estado", "enviado_at"])
        enviados += 1
    return enviados
