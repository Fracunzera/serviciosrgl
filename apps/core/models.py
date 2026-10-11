from django.db import models


class City(models.Model):
    nombre = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    # Las franjas de guardia se interpretan en la hora local de cada ciudad.
    timezone = models.CharField(max_length=64, default="America/Argentina/Rio_Gallegos")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "ciudad"
        verbose_name_plural = "ciudades"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Barrio(models.Model):
    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="barrios")
    nombre = models.CharField(max_length=100)
    slug = models.SlugField()

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(fields=["city", "slug"], name="barrio_slug_unico_por_ciudad"),
        ]

    def __str__(self):
        return self.nombre
