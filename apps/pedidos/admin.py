from django.contrib import admin, messages
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import Derivacion, FotoPedido, Mensaje, Pedido


class SoloLectura:
    """Pedidos y mensajes los genera el sistema: el admin mira, no edita."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class DerivacionInline(SoloLectura, admin.TabularInline):
    model = Derivacion
    extra = 0
    fields = ["posicion", "oficio", "estado", "canales", "enviado_at", "vence_at", "respondido_at", "monto", "mensaje"]
    readonly_fields = fields
    can_delete = False


class FotoPedidoInline(SoloLectura, admin.TabularInline):
    model = FotoPedido
    extra = 0
    fields = ["vista"]
    readonly_fields = ["vista"]
    can_delete = False

    @admin.display(description="Foto")
    def vista(self, obj):
        return format_html('<a href="{0}" target="_blank"><img src="{0}" style="height:80px"></a>', obj.imagen.url)


@admin.register(Pedido)
class PedidoAdmin(SoloLectura, admin.ModelAdmin):
    list_display = ["pk", "tipo", "rubro", "barrio", "estado", "sin_cubrir", "oficio_asignado", "creado_at"]
    list_filter = ["estado", "es_urgencia", "rubro", "city"]
    search_fields = ["nombre_vecino", "telefono_vecino", "email_vecino", "descripcion"]
    date_hierarchy = "creado_at"
    inlines = [DerivacionInline, FotoPedidoInline]
    actions = ["cerrar"]
    readonly_fields = ["seguimiento"]

    @admin.display(description="Tipo")
    def tipo(self, obj):
        return "URGENCIA" if obj.es_urgencia else "Presupuesto"

    @admin.display(description="Sin cubrir", boolean=True)
    def sin_cubrir(self, obj):
        return obj.es_urgencia and obj.estado == Pedido.Estado.ABIERTO and obj.fallback_at is not None

    @admin.display(description="Link del vecino")
    def seguimiento(self, obj):
        url = reverse("pedidos:seguimiento", args=[obj.token])
        return format_html('<a href="{}" target="_blank">ver lo que ve el vecino</a>', url)

    @admin.action(description="Cerrar (lo resolví por fuera)")
    def cerrar(self, request, queryset):
        n = 0
        for p in queryset.filter(estado=Pedido.Estado.ABIERTO):
            with transaction.atomic():
                p.estado = Pedido.Estado.CERRADO
                p.motivo_cierre = Pedido.MotivoCierre.ADMIN
                p.cerrado_at = timezone.now()
                p.save(update_fields=["estado", "motivo_cierre", "cerrado_at", "actualizado_at"])
                n += 1
        self.message_user(request, f"{n} pedido(s) cerrado(s).")


@admin.register(Mensaje)
class MensajeAdmin(SoloLectura, admin.ModelAdmin):
    list_display = ["creado_at", "canal", "destino", "asunto", "estado", "intentos", "enviado_at"]
    list_filter = ["estado", "canal"]
    search_fields = ["destino", "asunto"]
    actions = ["reintentar"]

    @admin.action(description="Reintentar ahora")
    def reintentar(self, request, queryset):
        n = queryset.exclude(estado=Mensaje.Estado.ENVIADO).update(
            estado=Mensaje.Estado.PENDIENTE, intentos=0, proximo_intento_at=timezone.now()
        )
        self.message_user(request, f"{n} mensaje(s) vuelven a la cola.", messages.SUCCESS)
