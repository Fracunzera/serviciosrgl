"""
Bot de Telegram, sin dependencias: la API es HTTP + JSON.

- Vinculación: el oficio abre t.me/<bot>?start=<token>. El token identifica
  al oficio y está firmado (HMAC con SECRET_KEY), así nadie puede vincular
  su chat al perfil de otro. Incluye version_links: revocar links también
  invalida el link de vinculación.
- Recepción: el worker hace getUpdates en cada vuelta (polling). No hace
  falta webhook ni exponer otro endpoint. Procesar dos veces un /start es
  inocuo.
- Envío: sender "telegram" de la bandeja. El botón abre el link firmado; la
  acción (aceptar, responder) se hace en la web, igual que desde el mail.
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.directorio.models import Oficio

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{metodo}"
SAL = "pedidos.telegram.vincular"

# Último update procesado. En memoria: si el worker se reinicia, Telegram
# reentrega los no confirmados y reprocesarlos no cambia nada.
_offset = None


class ErrorTelegram(Exception):
    def __init__(self, descripcion, codigo=None):
        super().__init__(descripcion)
        self.codigo = codigo
        # 403: el oficio bloqueó al bot. Reintentar no sirve.
        self.definitivo = codigo == 403


def activo():
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME)


def llamar(metodo, **params):
    req = urllib.request.Request(
        API.format(token=settings.TELEGRAM_BOT_TOKEN, metodo=metodo),
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            datos = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            datos = json.load(e)
        except ValueError:
            raise ErrorTelegram(f"HTTP {e.code}", e.code) from e
    if not datos.get("ok"):
        raise ErrorTelegram(datos.get("description", "error"), datos.get("error_code"))
    return datos["result"]


# --- Vinculación ---------------------------------------------------------------


def _firma(pk, version):
    return salted_hmac(SAL, f"{pk}:{version}").hexdigest()[:20]


def token_vinculacion(oficio):
    # Telegram acepta hasta 64 caracteres [A-Za-z0-9_-] en el parámetro start.
    return f"{oficio.pk}-{oficio.version_links}-{_firma(oficio.pk, oficio.version_links)}"


def link_vinculacion(oficio):
    if not activo():
        return ""
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token_vinculacion(oficio)}"


def oficio_por_token(token):
    try:
        pk, version, firma = token.split("-")
        pk, version = int(pk), int(version)
    except ValueError:
        return None
    if not constant_time_compare(firma, _firma(pk, version)):
        return None
    return Oficio.objects.filter(pk=pk, version_links=version).first()


# --- Recepción (worker) --------------------------------------------------------

AYUDA = (
    "Hola, soy el bot de Servicios RGL. Por acá te llegan los pedidos y urgencias de la guía.\n"
    "Para vincular tu cuenta, abrí el link de Telegram que te mandamos por mail."
)


def procesar_actualizaciones():
    global _offset
    if not activo():
        return 0
    params = {"timeout": 0, "allowed_updates": ["message"]}
    if _offset is not None:
        params["offset"] = _offset
    n = 0
    for update in llamar("getUpdates", **params):
        _offset = update["update_id"] + 1
        msg = update.get("message") or {}
        chat_id, texto = (msg.get("chat") or {}).get("id"), (msg.get("text") or "").strip()
        if chat_id is None:
            continue
        _responder(chat_id, texto)
        n += 1
    return n


def _responder(chat_id, texto):
    partes = texto.split(maxsplit=1)
    if partes and partes[0] == "/start" and len(partes) == 2:
        oficio = oficio_por_token(partes[1])
        if oficio is None:
            respuesta = "Ese link no es válido o ya venció. Pedile uno nuevo al administrador de la guía."
        else:
            # Un chat, un oficio: si este chat estaba vinculado a otro perfil, se libera.
            Oficio.objects.filter(telegram_chat_id=str(chat_id)).exclude(pk=oficio.pk).update(telegram_chat_id="")
            Oficio.objects.filter(pk=oficio.pk).update(telegram_chat_id=str(chat_id))
            respuesta = (
                f"¡Listo, {oficio.nombre}! Desde ahora los pedidos y urgencias de la guía te llegan acá. "
                "Para dejar de recibirlos, desvinculá Telegram desde tu link de disponibilidad."
            )
    else:
        respuesta = AYUDA
    try:
        llamar("sendMessage", chat_id=chat_id, text=respuesta)
    except ErrorTelegram as e:
        log.warning("No pude responder en Telegram a %s: %s", chat_id, e)


# --- Envío (sender de la bandeja) ----------------------------------------------


def enviar(mensaje):
    params = {"chat_id": mensaje.destino, "text": f"{mensaje.asunto}\n\n{mensaje.texto}"[:4096]}
    if mensaje.link:
        params["reply_markup"] = {"inline_keyboard": [[{"text": "Abrir y responder", "url": mensaje.link}]]}
    try:
        llamar("sendMessage", **params)
    except ErrorTelegram as e:
        if e.definitivo:
            # Bloqueó al bot: se desvincula y los próximos avisos vuelven a
            # salir por mail y semimanual.
            Oficio.objects.filter(telegram_chat_id=mensaje.destino).update(telegram_chat_id="")
        raise
