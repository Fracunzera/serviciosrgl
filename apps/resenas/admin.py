from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from .models import Reclamo, Reporte, Resena


@admin.register(Resena)
class ResenaAdmin(admin.ModelAdmin):
    """Para cargar reseñas de arranque: Agregar → oficio, autor, estrellas,
    texto y la fecha real del trabajo. Sin pedido = bootstrap."""

    list_display = ["oficio", "estrellas", "autor", "origen", "estado", "creado_at"]
    list_filter = ["estado", "bootstrap", "estrellas"]
    search_fields = ["autor", "texto", "oficio__nombre"]
    autocomplete_fields = ["oficio"]
    actions = ["ocultar", "publicar"]
    fields = ["oficio", "autor", "estrellas", "texto", "creado_at", "estado", "motivo_moderacion", "pedido", "bootstrap"]
    readonly_fields = ["pedido", "bootstrap"]

    @admin.display(description="Origen")
    def origen(self, obj):
        return "Bootstrap (cargada por el admin)" if obj.bootstrap else f"Pedido #{obj.pedido_id}"

    def save_model(self, request, obj, form, change):
        if not obj.pedido_id:
            obj.bootstrap = True
        super().save_model(request, obj, form, change)

    @admin.action(description="Ocultar (moderación)")
    def ocultar(self, request, queryset):
        n = queryset.update(estado=Resena.Estado.OCULTA)
        self.message_user(request, f"{n} reseña(s) ocultas. Podés anotar el motivo en cada una.", messages.WARNING)

    @admin.action(description="Volver a publicar")
    def publicar(self, request, queryset):
        n = queryset.update(estado=Resena.Estado.PUBLICADA)
        self.message_user(request, f"{n} reseña(s) publicadas.")


@admin.register(Reclamo)
class ReclamoAdmin(admin.ModelAdmin):
    list_display = ["creado_at", "oficio", "pedido_link", "estado"]
    list_filter = ["estado"]
    search_fields = ["texto", "pedido__oficio_asignado__nombre"]
    fields = ["pedido_link", "oficio", "texto", "estado", "nota_admin", "creado_at"]
    readonly_fields = ["pedido_link", "oficio", "texto", "creado_at"]
    actions = ["resolver"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Oficio")
    def oficio(self, obj):
        o = obj.pedido.oficio_asignado
        if not o:
            return "—"
        url = reverse("admin:directorio_oficio_change", args=[o.pk])
        return format_html('<a href="{}">{}</a> (para suspender: acción «Suspender» en la lista de oficios)', url, o)

    @admin.display(description="Pedido")
    def pedido_link(self, obj):
        url = reverse("admin:pedidos_pedido_change", args=[obj.pedido_id])
        return format_html('<a href="{}">#{}</a>', url, obj.pedido_id)

    @admin.action(description="Marcar resuelto")
    def resolver(self, request, queryset):
        queryset.update(estado=Reclamo.Estado.RESUELTO)


@admin.register(Reporte)
class ReporteAdmin(admin.ModelAdmin):
    list_display = ["creado_at", "tipo", "nombre_oficio", "rubro", "oficio", "estado"]
    list_filter = ["tipo", "estado"]
    search_fields = ["nombre_oficio", "texto", "telefono_oficio"]
    autocomplete_fields = ["oficio"]
    actions = ["revisado"]
    readonly_fields = ["tipo", "nombre_oficio", "rubro", "telefono_oficio", "texto", "contacto", "creado_at"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Marcar revisado")
    def revisado(self, request, queryset):
        queryset.update(estado=Reporte.Estado.REVISADO)
