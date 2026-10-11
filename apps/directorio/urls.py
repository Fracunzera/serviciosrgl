from django.urls import path

from . import views

app_name = "directorio"

urlpatterns = [
    path("rubro/<slug:slug>/", views.rubro, name="rubro"),
    path("oficio/<slug:slug>/", views.oficio, name="oficio"),
]
