import secrets
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import Barrio, City
from apps.directorio.models import Oficio, Rubro


def _token():
    return secrets.token_urlsafe(16)


class Pedido(models.Model):
    """Un pedido del vecino. Dos modos con el mismo modelo:
    - presupuesto (es_urgencia=False): fan-out a los oficios de la zona.
    - urgencia (es_urgencia=True): escalado uno por uno entre los de guardia.
    """

    class Estado(models.TextChoices):
        ABIERTO = "abierto", "Abierto"
        ASIGNADO = "asignado", "Asignado"
        COMPLETADO = "completado", "Completado"
        CERRADO = "cerrado", "Cerrado sin trabajo"

    class Completado(models.TextChoices):
        VECINO = "vecino", "Lo confirmó el vecino"
        AUTO = "auto", "Automático (sin respuesta del vecino)"

    class MotivoCierre(models.TextChoices):
        CANCELADO = "cancelado", "Cancelado por el vecino"
        SIN_RESPUESTA = "sin_respuesta", "Ningún oficio respondió"
        ADMIN = "admin", "Cerrado por el admin"

    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="pedidos")
    rubro = models.ForeignKey(Rubro, on_delete=models.PROTECT, related_name="pedidos")
    barrio = models.ForeignKey(Barrio, on_delete=models.PROTECT, related_name="pedidos")
    descripcion = models.TextField()
    es_urgencia = models.BooleanField(default=False)

    # Contacto del vecino: privado. Se comparte con el oficio recién cuando
    # acepta (urgencia) o cuando el vecino lo elige (presupuesto).
    nombre_vecino = models.CharField(max_length=100)
    telefono_vecino = models.CharField(max_length=30)
    # Obligatorio en presupuesto (por ahí le avisamos las respuestas),
    # opcional en urgencia (no frenamos a alguien con el agua corriendo).
    email_vecino = models.EmailField(blank=True)
    direccion = models.CharField(max_length=200, blank=True)

    # Link de seguimiento del vecino (/s/<token>/). Token aleatorio guardado,
    # no firmado: tiene que durar meses (para la reseña) y sobrevivir a una
    # rotación de SECRET_KEY.
    token = models.CharField(max_length=32, unique=True, default=_token, editable=False)

    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.ABIERTO)
    oficio_asignado = models.ForeignKey(
        Oficio, null=True, blank=True, on_delete=models.PROTECT, related_name="pedidos_asignados"
    )
    asignado_at = models.DateTimeField(null=True, blank=True)
    completado_at = models.DateTimeField(null=True, blank=True)
    completado_por = models.CharField(max_length=10, choices=Completado.choices, blank=True)
    cerrado_at = models.DateTimeField(null=True, blank=True)
    motivo_cierre = models.CharField(max_length=15, choices=MotivoCierre.choices, blank=True)
    # Urgencia: momento en que se agotó la cola y se mostró el fallback.
    fallback_at = models.DateTimeField(null=True, blank=True)
    # Datos del vecino borrados (ver privacidad.anonimizar_viejos).
    anonimizado_at = models.DateTimeField(null=True, blank=True)

    creado_at = models.DateTimeField(auto_now_add=True)
    actualizado_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creado_at"]
        indexes = [models.Index(fields=["estado", "es_urgencia"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(estado__in=["abierto", "cerrado"]) | Q(oficio_asignado__isnull=False),
                name="pedido_asignado_tiene_oficio",
            ),
        ]

    def __str__(self):
        tipo = "Urgencia" if self.es_urgencia else "Presupuesto"
        return f"{tipo} #{self.pk} — {self.rubro} en {self.barrio}"

    def clean(self):
        if self.barrio_id and self.city_id and self.barrio.city_id != self.city_id:
            raise ValidationError({"barrio": "El barrio no pertenece a la ciudad del pedido."})
        if not self.es_urgencia and not self.email_vecino:
            raise ValidationError({"email_vecino": "Necesitamos un mail para avisarte las respuestas."})

    @property
    def vence_autocompletado(self):
        """Si el vecino no confirma, el worker lo completa solo a esta hora."""
        if self.estado != self.Estado.ASIGNADO or not self.asignado_at:
            return None
        return self.asignado_at + timedelta(hours=settings.PEDIDO_HORAS_AUTOCOMPLETAR)


def _ruta_foto(instance, filename):
    return f"pedidos/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class FotoPedido(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="fotos")
    imagen = models.ImageField(upload_to=_ruta_foto)

    class Meta:
        verbose_name = "foto del pedido"
        verbose_name_plural = "fotos del pedido"


class Derivacion(models.Model):
    """Un pedido derivado a un oficio: una fila por (pedido, oficio).

    Es el IntentoNotificacion del spec, que además guarda la respuesta del
    oficio (en presupuesto: mensaje y monto). El link firmado lleva el id de
    la derivación + Oficio.version_links; no hace falta guardar el token.
    """

    class Estado(models.TextChoices):
        ENVIADA = "enviada", "Enviada, sin respuesta"
        RESPONDIDA = "respondida", "Respondió (presupuesto)"
        ELEGIDA = "elegida", "Elegida / aceptada"
        RECHAZADA = "rechazada", "Rechazó"
        VENCIDA = "vencida", "Venció el plazo (urgencia)"
        ERROR = "error", "Error al enviar"

    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="derivaciones")
    oficio = models.ForeignKey(Oficio, on_delete=models.PROTECT, related_name="derivaciones")
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.ENVIADA)
    # Urgencia: lugar en la cola (1 = primero). Null en presupuesto.
    posicion = models.PositiveSmallIntegerField(null=True, blank=True)
    # Backends por los que salió, ej. "email,semimanual".
    canales = models.CharField(max_length=100, blank=True)
    error = models.TextField(blank=True)

    enviado_at = models.DateTimeField(auto_now_add=True)
    # Urgencia: si a esta hora sigue ENVIADA, el worker escala.
    vence_at = models.DateTimeField(null=True, blank=True)
    respondido_at = models.DateTimeField(null=True, blank=True)

    # Respuesta de presupuesto.
    mensaje = models.TextField(blank=True)
    monto = models.PositiveIntegerField("monto estimado ($)", null=True, blank=True)

    class Meta:
        verbose_name = "derivación"
        verbose_name_plural = "derivaciones"
        ordering = ["pedido", "posicion", "enviado_at"]
        indexes = [models.Index(fields=["estado", "vence_at"])]
        constraints = [
            models.UniqueConstraint(fields=["pedido", "oficio"], name="una_derivacion_por_oficio"),
        ]

    def __str__(self):
        return f"Pedido #{self.pedido_id} → {self.oficio} ({self.get_estado_display()})"


class Mensaje(models.Model):
    """Bandeja de salida. La web solo encola; el worker envía y reintenta.

    Así un SMTP lento nunca retiene el lock de escritura de SQLite dentro de
    un request, y si el SMTP se cae los avisos salen cuando vuelve.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        ENVIADO = "enviado", "Enviado"
        FALLIDO = "fallido", "Falló (sin más reintentos)"

    canal = models.CharField(max_length=20, default="email")
    destino = models.CharField(max_length=200, help_text="Mail, o chat id para otros canales.")
    asunto = models.CharField(max_length=200)
    texto = models.TextField()
    derivacion = models.ForeignKey(
        Derivacion, null=True, blank=True, on_delete=models.SET_NULL, related_name="mensajes"
    )
    pedido = models.ForeignKey(
        Pedido, null=True, blank=True, on_delete=models.SET_NULL, related_name="mensajes"
    )

    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    intentos = models.PositiveSmallIntegerField(default=0)
    proximo_intento_at = models.DateTimeField(default=timezone.now)
    ultimo_error = models.TextField(blank=True)
    creado_at = models.DateTimeField(auto_now_add=True)
    enviado_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "mensaje saliente"
        verbose_name_plural = "mensajes salientes"
        ordering = ["-creado_at"]
        indexes = [models.Index(fields=["estado", "proximo_intento_at"])]

    def __str__(self):
        return f"[{self.canal}] {self.asunto} → {self.destino}"
