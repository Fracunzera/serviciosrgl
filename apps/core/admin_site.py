from datetime import timedelta

from django.contrib import admin
from django.urls import reverse
from django.utils import timezone


class GuiaAdminSite(admin.AdminSite):
    site_header = "Servicios RGL · administración"
    site_title = "Servicios RGL"
    index_title = "Panel"
    index_template = "admin/guia_index.html"

    def index(self, request, extra_context=None):
        extra_context = {**(extra_context or {}), "para_revisar": self.para_revisar()}
        return super().index(request, extra_context)

    def para_revisar(self):
        """Lo que pide atención. Si todo está en cero, no hay nada que hacer hoy."""
        from apps.directorio.models import Oficio
        from apps.pedidos.models import Mensaje, Pedido
        from apps.resenas.models import Reclamo, Reporte

        hace_10_min = timezone.now() - timedelta(minutes=10)
        items = [
            ("Oficios para aprobar", Oficio.objects.filter(estado="pendiente").count(),
             "directorio_oficio", "estado__exact=pendiente"),
            ("Urgencias sin cubrir", Pedido.objects.filter(
                es_urgencia=True, estado="abierto", fallback_at__isnull=False).count(),
             "pedidos_pedido", "es_urgencia__exact=1&estado__exact=abierto&fallback_at__isnull=False"),
            ("Reclamos abiertos", Reclamo.objects.filter(estado="abierto").count(),
             "resenas_reclamo", "estado__exact=abierto"),
            ("Recomendaciones y advertencias nuevas", Reporte.objects.filter(estado="nuevo").count(),
             "resenas_reporte", "estado__exact=nuevo"),
            ("Mensajes que fallaron", Mensaje.objects.filter(estado="fallido").count(),
             "pedidos_mensaje", "estado__exact=fallido"),
            # Si hay pendientes viejos, el worker no está corriendo.
            ("Mensajes trabados (¿anda el worker?)", Mensaje.objects.filter(
                estado="pendiente", proximo_intento_at__lte=hace_10_min).count(),
             "pedidos_mensaje", "estado__exact=pendiente"),
        ]
        return [
            {"texto": t, "cantidad": n, "url": reverse(f"admin:{m}_changelist") + "?" + q}
            for t, n, m, q in items
        ]
