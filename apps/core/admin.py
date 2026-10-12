from django.contrib import admin

from .models import Barrio, City


class BarrioInline(admin.TabularInline):
    model = Barrio
    extra = 0
    prepopulated_fields = {"slug": ["nombre"]}


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ["nombre", "slug", "activa"]
    prepopulated_fields = {"slug": ["nombre"]}
    inlines = [BarrioInline]
