import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import models
from django.db.models import Avg, Count, Q
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import Barrio, City


class Rubro(models.Model):
    nombre = models.CharField(max_length=80)
    slug = models.SlugField(unique=True)
    # Gas y electricidad: sin matrícula no se aprueba el perfil.
    requiere_matricula = models.BooleanField(default=False)
    # Aparece como opción en la línea de urgencias.
    en_urgencias = models.BooleanField(default=False)
    # Para mapear texto libre a rubro ("pérdida, caño, canilla"). Editable
    # desde el admin: afinar el mapeo no requiere deploy.
    palabras_clave = models.TextField(
        blank=True, help_text="Separadas por coma. Se usan para detectar el rubro en urgencias."
    )
    # El "Ver también" de la guía: Plomería ↔ Destapaciones, Gas ↔ Plomería.
    ver_tambien = models.ManyToManyField("self", blank=True)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["orden", "nombre"]

    def __str__(self):
        return self.nombre

    def lista_palabras_clave(self):
        return [p.strip().lower() for p in self.palabras_clave.split(",") if p.strip()]


class OficioQuerySet(models.QuerySet):
    def publicados(self):
        """Los que figuran en el directorio: solo aprobados."""
        return self.filter(estado=Oficio.Estado.APROBADO)

    def con_rating(self):
        publicadas = Q(resenas__estado="publicada")
        return self.annotate(
            rating=Avg("resenas__estrellas", filter=publicadas),
            cantidad_resenas=Count("resenas", filter=publicadas, distinct=True),
        )


class Oficio(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de aprobación"
        APROBADO = "aprobado", "Aprobado"
        SUSPENDIDO = "suspendido", "Suspendido"

    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="oficios")
    nombre = models.CharField(max_length=120)
    # Como en la guía: apellido primero ("Acosta, Ramón"). Ordena el
    # listado. Vacío para comercios ("Cerrajería La Llave").
    nombre_guia = models.CharField(
        "nombre en la guía", max_length=120, blank=True,
        help_text='Apellido primero, separado por coma: "Acosta, Ramón". Vacío = se usa el nombre.',
    )
    slug = models.SlugField(max_length=140, blank=True)
    rubros = models.ManyToManyField(Rubro, related_name="oficios")
    cubre_toda_la_ciudad = models.BooleanField(default=False)
    barrios = models.ManyToManyField(Barrio, blank=True, related_name="oficios")
    anios_oficio = models.PositiveSmallIntegerField("años de oficio", null=True, blank=True)
    garantia = models.CharField(max_length=200, blank=True, help_text="Ej.: 6 meses en mano de obra")
    descripcion = models.TextField(blank=True)

    # Privados: nunca se muestran salvo el teléfono en el fallback, y solo
    # con consentimiento (ver Consentimiento).
    telefono = models.CharField(max_length=30)
    email = models.EmailField(help_text="Por acá le llegan los pedidos y el link mágico.")

    matricula = models.CharField(max_length=60, blank=True)
    # No guardamos el número de DNI (minimización de datos, Ley 25.326):
    # solo cuándo se verificó la identidad.
    dni_verificado_at = models.DateTimeField(null=True, blank=True)

    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    aprobado_at = models.DateTimeField(null=True, blank=True)
    suspendido_at = models.DateTimeField(null=True, blank=True)
    motivo_suspension = models.TextField(blank=True)

    # Guardias
    de_guardia = models.BooleanField(default=False)
    no_disponible_hasta = models.DateTimeField(null=True, blank=True)
    # Rotación: la urgencia va primero a quien recibió una hace más tiempo.
    ultima_asignacion = models.DateTimeField(null=True, blank=True)
    # Caché del último Consentimiento de tipo fallback (el historial está allá).
    telefono_en_fallback = models.BooleanField(default=False)

    # Va dentro de cada link firmado. Incrementarlo invalida todos los links
    # vigentes del oficio (p. ej. si reenvió un mail a quien no debía).
    version_links = models.PositiveIntegerField(default=1)

    creado_at = models.DateTimeField(auto_now_add=True)
    actualizado_at = models.DateTimeField(auto_now=True)

    objects = OficioQuerySet.as_manager()

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(fields=["city", "slug"], name="oficio_slug_unico_por_ciudad"),
        ]

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._slug_libre()
        super().save(*args, **kwargs)

    def _slug_libre(self):
        base = slugify(self.nombre)[:130] or "oficio"
        slug, n = base, 2
        while Oficio.objects.filter(city_id=self.city_id, slug=slug).exclude(pk=self.pk).exists():
            slug, n = f"{base}-{n}", n + 1
        return slug

    @property
    def partes_guia(self):
        """("Acosta", "Ramón") para "Acosta, Ramón"; (nombre, "") si no hay nombre_guia."""
        principal, _, resto = (self.nombre_guia or self.nombre).partition(",")
        return principal.strip(), resto.strip()

    @property
    def verificado(self):
        """El sello: aprobado por el admin con identidad verificada."""
        return self.estado == self.Estado.APROBADO and self.dni_verificado_at is not None

    def falta_matricula(self):
        """True si algún rubro exige matrícula y no está cargada. (M2M: llamar con el oficio guardado.)"""
        return not self.matricula and self.rubros.filter(requiere_matricula=True).exists()

    def disponible(self, ahora=None):
        ahora = ahora or timezone.now()
        return self.no_disponible_hasta is None or self.no_disponible_hasta <= ahora

    def de_guardia_en(self, ahora=None):
        """¿Puede recibir una urgencia en este momento? (sin mirar el rubro)."""
        ahora = ahora or timezone.now()
        if self.estado != self.Estado.APROBADO or not self.de_guardia or not self.disponible(ahora):
            return False
        local = ahora.astimezone(ZoneInfo(self.city.timezone))
        return any(f.cubre(local) for f in self.franjas.all())


class FranjaGuardia(models.Model):
    """Franja semanal habitual. Si hasta < desde, cruza la medianoche
    (vie 22:00 → sáb 06:00). Si desde == hasta, es el día completo."""

    class Dia(models.IntegerChoices):
        LUNES = 0, "Lunes"
        MARTES = 1, "Martes"
        MIERCOLES = 2, "Miércoles"
        JUEVES = 3, "Jueves"
        VIERNES = 4, "Viernes"
        SABADO = 5, "Sábado"
        DOMINGO = 6, "Domingo"

    oficio = models.ForeignKey(Oficio, on_delete=models.CASCADE, related_name="franjas")
    dia = models.PositiveSmallIntegerField(choices=Dia.choices)
    desde = models.TimeField()
    hasta = models.TimeField()

    class Meta:
        verbose_name = "franja de guardia"
        verbose_name_plural = "franjas de guardia"
        ordering = ["dia", "desde"]

    def __str__(self):
        return f"{self.get_dia_display()} {self.desde:%H:%M}–{self.hasta:%H:%M}"

    def cubre(self, local_dt):
        """local_dt ya convertido a la hora de la ciudad."""
        dia, hora = local_dt.weekday(), local_dt.time()
        if self.desde == self.hasta:
            return dia == self.dia
        if self.desde < self.hasta:
            return dia == self.dia and self.desde <= hora < self.hasta
        # Cruza medianoche.
        return (dia == self.dia and hora >= self.desde) or (
            dia == (self.dia + 1) % 7 and hora < self.hasta
        )


class Consentimiento(models.Model):
    """Registro de qué aceptó el oficio y cuándo (Ley 25.326). Append-only:
    revocar es un registro nuevo con otorgado=False."""

    class Tipo(models.TextChoices):
        TELEFONO_FALLBACK = "telefono_fallback", "Mostrar mi teléfono en el fallback de urgencias"

    oficio = models.ForeignKey(Oficio, on_delete=models.CASCADE, related_name="consentimientos")
    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    otorgado = models.BooleanField()
    # Texto exacto que vio, por si cambia la redacción después.
    texto = models.TextField()
    creado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_at"]

    @classmethod
    def registrar(cls, oficio, tipo, otorgado, texto):
        c = cls.objects.create(oficio=oficio, tipo=tipo, otorgado=otorgado, texto=texto)
        if tipo == cls.Tipo.TELEFONO_FALLBACK:
            oficio.telefono_en_fallback = otorgado
            oficio.save(update_fields=["telefono_en_fallback", "actualizado_at"])
        return c


def _ruta_foto(instance, filename):
    # Nombre aleatorio: no expone el nombre original ni es adivinable.
    return f"trabajos/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class FotoTrabajo(models.Model):
    oficio = models.ForeignKey(Oficio, on_delete=models.CASCADE, related_name="fotos")
    imagen = models.ImageField(upload_to=_ruta_foto)
    descripcion = models.CharField(max_length=200, blank=True)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "foto de trabajo"
        verbose_name_plural = "fotos de trabajos"
        ordering = ["orden", "id"]
