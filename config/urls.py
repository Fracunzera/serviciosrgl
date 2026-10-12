from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve

from apps.core import views as core_views


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("", core_views.raiz),
    # Antes que las rutas de ciudad: s/, p/ y o/ no son ciudades.
    path("", include("apps.pedidos.urls")),
    path("", include("apps.resenas.urls")),
    # Todo lo público cuelga de la ciudad: /rio-gallegos/...
    path("<slug:city_slug>/", include("apps.core.urls")),
    path("<slug:city_slug>/", include("apps.directorio.urls")),
]

if settings.SERVE_MEDIA:
    # Fallback para setups sin reverse proxy sirviendo /media/.
    # Antes que <city_slug>/ para que "media" no se tome como ciudad.
    urlpatterns.insert(
        0, re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})
    )
