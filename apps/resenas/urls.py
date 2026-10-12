from django.urls import path

from . import views

app_name = "resenas"

urlpatterns = [
    path("s/<str:token>/resena/", views.resena, name="resena"),
    path("s/<str:token>/reclamo/", views.reclamo, name="reclamo"),
]
