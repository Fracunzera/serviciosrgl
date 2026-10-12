"""
Settings de Servicios RGL.

Todo lo que cambia entre local y producción viene de variables de entorno
(ver .env.example). No hay settings_dev / settings_prod: un solo archivo.
"""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Falta DJANGO_SECRET_KEY")
    SECRET_KEY = "dev-inseguro-no-usar-en-produccion"
# Los links firmados (aceptar pedido, disponibilidad, link mágico) dependen
# de SECRET_KEY. Para rotarla sin invalidar links en vuelo, poné la vieja acá.
SECRET_KEY_FALLBACKS = env_list("DJANGO_SECRET_KEY_FALLBACKS")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost")
# El healthcheck de Docker pega a 127.0.0.1; el puerto solo escucha en
# localhost del host, así que no abre nada hacia afuera.
ALLOWED_HOSTS.append("127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# URL pública absoluta. El worker no atiende requests, así que no puede
# deducir el dominio: la necesita para armar los links de los mails.
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")

INSTALLED_APPS = [
    "apps.core.admin_apps.GuiaAdminConfig",  # admin con tablero "Para revisar"
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.directorio",
    "apps.pedidos",
    "apps.resenas",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.guia",
            ],
        },
    },
]

# --- Base de datos -----------------------------------------------------------
# SQLite en WAL. DATA_DIR es un *directorio* montado como volumen local por
# web y worker: WAL necesita los archivos -wal y -shm al lado de la base, por
# eso se monta la carpeta y no el archivo suelto.
DATA_DIR = Path(env("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)  # SQLite no crea directorios

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        "OPTIONS": {
            # busy_timeout: si la base está bloqueada, espera hasta 5 s.
            "timeout": 5,
            # IMMEDIATE toma el lock de escritura al abrir la transacción.
            # Con el modo por defecto (DEFERRED), una transacción que primero
            # lee y después escribe falla al instante con "database is locked"
            # sin respetar el busy_timeout. Con dos procesos escribiendo
            # (web + worker) esto importa.
            "transaction_mode": "IMMEDIATE",
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA foreign_keys=ON;"
            ),
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Idioma y hora -----------------------------------------------------------
LANGUAGE_CODE = "es-ar"
# Las franjas de guardia ("sáb 8–22") se interpretan en hora local.
TIME_ZONE = "America/Argentina/Rio_Gallegos"
USE_I18N = True
USE_TZ = True

# --- Estáticos (whitenoise) y media ------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media (fotos de trabajos y de pedidos) vive en el mismo volumen que la base.
# whitenoise NO sirve media: en producción la sirve el reverse proxy desde
# DATA_DIR/media (o Django si SERVE_MEDIA=1, para setups sin proxy).
MEDIA_URL = "/media/"
MEDIA_ROOT = DATA_DIR / "media"
SERVE_MEDIA = env_bool("SERVE_MEDIA", DEBUG)

# El storage con manifest exige collectstatic previo: solo en producción
# (en la imagen Docker se corre al construir). En desarrollo y tests, simple.
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG or TESTING
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Fotos: el límite real lo pone la validación del form; esto corta antes
# de que un upload gigante llegue a memoria.
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o644

# --- Mail --------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Servicios RGL <no-responder@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# A quién avisar en el fallback de urgencias, reclamos y reportes.
ADMIN_EMAIL = env("ADMIN_EMAIL", "")
ADMINS = [("Admin", ADMIN_EMAIL)] if ADMIN_EMAIL else []

# --- Producto ----------------------------------------------------------------
DEFAULT_CITY_SLUG = "rio-gallegos"
# Backends del notificador, en orden. Se agregan "telegram" / "whatsapp" sin
# tocar el ruteo.
NOTIFICADORES = env_list("NOTIFICADORES", "email,semimanual,telegram")
# Bot de Telegram (BotFather). Vacío = Telegram apagado.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", "").lstrip("@")
# Minutos que tiene un oficio de guardia para aceptar antes de escalar.
URGENCIA_MINUTOS_PARA_ACEPTAR = int(env("URGENCIA_MINUTOS_PARA_ACEPTAR", "10"))
# Si el vecino no confirma que se hizo el trabajo, se completa solo
# (y queda habilitada la reseña) a las N horas de asignado.
PEDIDO_HORAS_AUTOCOMPLETAR = int(env("PEDIDO_HORAS_AUTOCOMPLETAR", "24"))
# Cada cuántos segundos el worker revisa pedidos vencidos.
# Bajo porque el worker también despacha los mails (outbox): es la demora
# máxima entre que se crea un aviso y sale.
WORKER_INTERVALO_SEGUNDOS = int(env("WORKER_INTERVALO_SEGUNDOS", "10"))
# Vigencia de los links firmados que reciben los oficios.
LINKS_OFICIO_DIAS = int(env("LINKS_OFICIO_DIAS", "60"))
# Antispam: pedidos por teléfono por hora.
PEDIDOS_POR_TELEFONO_POR_HORA = 3
FACEBOOK_GRUPO_URL = env("FACEBOOK_GRUPO_URL", "")
# Pasados N días de cerrado/completado, se borran los datos del vecino del
# pedido (nombre, teléfono, mail, dirección, descripción, fotos) y los mails
# enviados. Quedan rubro, barrio, fechas y la reseña. Lo promete el aviso de
# privacidad: si se cambia, actualizar el texto.
PEDIDOS_ANONIMIZAR_DIAS = int(env("PEDIDOS_ANONIMIZAR_DIAS", "365"))

# Datos del responsable para Términos y Aviso de privacidad (Ley 25.326).
TITULAR_NOMBRE = env("TITULAR_NOMBRE", "[COMPLETAR: nombre o razón social]")
TITULAR_DOMICILIO = env("TITULAR_DOMICILIO", "[COMPLETAR: domicilio], Río Gallegos, Santa Cruz")
CONTACTO_EMAIL = env("CONTACTO_EMAIL", ADMIN_EMAIL or "[COMPLETAR: mail de contacto]")

# --- Seguridad detrás del reverse proxy --------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # HSTS corto al principio; subirlo cuando el dominio esté estable.
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "3600"))

# La redirección http→https la hace el reverse proxy. Si la hiciera Django,
# rompería el healthcheck, que pega por http a 127.0.0.1.
SILENCED_SYSTEM_CHECKS = ["security.W008"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
