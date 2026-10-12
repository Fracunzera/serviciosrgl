from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("nosotros/", views.pagina, {"plantilla": "nosotros"}, name="nosotros"),
    path("contacto/", views.pagina, {"plantilla": "contacto"}, name="contacto"),
    path("terminos/", views.pagina, {"plantilla": "terminos"}, name="terminos"),
    path("privacidad/", views.pagina, {"plantilla": "privacidad"}, name="privacidad"),
]
