from django.urls import path

from . import views

app_name = "pedidos"

urlpatterns = [
    path("<slug:city_slug>/presupuesto/", views.presupuesto, name="presupuesto"),
    path("<slug:city_slug>/urgencia/", views.urgencia, name="urgencia"),
    # Links sin ciudad: el token ya identifica todo.
    path("s/<str:token>/", views.seguimiento, name="seguimiento"),
    path("p/<str:firma>/", views.derivacion, name="derivacion"),
    path("o/<str:firma>/", views.disponibilidad, name="disponibilidad"),
]
