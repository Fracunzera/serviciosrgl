from django.urls import path

from . import views
from .models import Reporte

app_name = "resenas"

urlpatterns = [
    path("s/<str:token>/resena/", views.resena, name="resena"),
    path("s/<str:token>/reclamo/", views.reclamo, name="reclamo"),
    path("<slug:city_slug>/recomenda/", views.reporte, {"tipo": Reporte.Tipo.RECOMENDACION}, name="recomenda"),
    path("<slug:city_slug>/adverti/", views.reporte, {"tipo": Reporte.Tipo.ADVERTENCIA}, name="adverti"),
]
