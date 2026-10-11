from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import City
from apps.directorio.models import Oficio
from apps.pedidos.models import Pedido


class Resena(models.Model):
    """Reseña pública. Sale de un pedido completado (una por pedido) o la
    carga el admin como bootstrap. Se publica al toque y el admin puede
    ocultarla: moderación posterior, no hay cola diaria que revisar."""

    class Estado(models.TextChoices):
        PUBLICADA = "publicada", "Publicada"
        OCULTA = "oculta", "Oculta por moderación"

    oficio = models.ForeignKey(Oficio, on_delete=models.CASCADE, related_name="resenas")
    # PROTECT: un pedido con reseña no se puede borrar (se anonimiza).
    pedido = models.OneToOneField(
        Pedido, null=True, blank=True, on_delete=models.PROTECT, related_name="resena"
    )
    bootstrap = models.BooleanField(
        default=False, help_text="Cargada por el admin a partir de un trabajo verificado por fuera."
    )
    autor = models.CharField(max_length=80, help_text='Lo que se muestra, ej. "Marcela, Barrio Belgrano"')
    estrellas = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    texto = models.TextField(blank=True)

    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PUBLICADA)
    motivo_moderacion = models.TextField(blank=True)
    creado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "reseña"
        ordering = ["-creado_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(estrellas__gte=1, estrellas__lte=5), name="resena_estrellas_1_a_5"
            ),
            # Sin pedido solo si es de bootstrap.
            models.CheckConstraint(
                condition=Q(pedido__isnull=False) | Q(bootstrap=True),
                name="resena_con_pedido_o_bootstrap",
            ),
        ]

    def clean(self):
        if self.pedido_id:
            p = self.pedido
            if p.estado != Pedido.Estado.COMPLETADO:
                raise ValidationError("Solo se puede reseñar un pedido completado.")
            if p.oficio_asignado_id != self.oficio_id:
                raise ValidationError("La reseña tiene que ser del oficio que hizo el trabajo.")

    def __str__(self):
        return f"{self.estrellas}★ a {self.oficio} — {self.autor}"


class Reclamo(models.Model):
    """El vecino reclama sobre un pedido (desde su link de seguimiento)."""

    class Estado(models.TextChoices):
        ABIERTO = "abierto", "Abierto"
        RESUELTO = "resuelto", "Resuelto"

    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="reclamos")
    texto = models.TextField()
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ABIERTO)
    nota_admin = models.TextField(blank=True)
    creado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["estado", "-creado_at"]

    def __str__(self):
        return f"Reclamo sobre pedido #{self.pedido_id}"


class Reporte(models.Model):
    """Formularios públicos "Recomendá un oficio" y "Advertí sobre un oficio"."""

    class Tipo(models.TextChoices):
        RECOMENDACION = "recomendacion", "Recomendación"
        ADVERTENCIA = "advertencia", "Advertencia"

    class Estado(models.TextChoices):
        NUEVO = "nuevo", "Nuevo"
        REVISADO = "revisado", "Revisado"

    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="reportes")
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    # Texto libre: el oficio puede no estar en la plataforma todavía.
    nombre_oficio = models.CharField(max_length=120)
    oficio = models.ForeignKey(
        Oficio, null=True, blank=True, on_delete=models.SET_NULL, related_name="reportes",
        help_text="Lo vincula el admin si el oficio ya está cargado.",
    )
    rubro = models.CharField(max_length=80, blank=True)
    telefono_oficio = models.CharField(max_length=30, blank=True)
    texto = models.TextField()
    contacto = models.CharField(max_length=120, blank=True, help_text="Opcional, por si hay que repreguntar.")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.NUEVO)
    nota_admin = models.TextField(blank=True)
    creado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["estado", "-creado_at"]

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.nombre_oficio}"
