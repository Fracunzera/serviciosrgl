from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import path, re_path
from django.views.static import serve


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
]

if settings.SERVE_MEDIA:
    # Fallback para setups sin reverse proxy sirviendo /media/.
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ]
