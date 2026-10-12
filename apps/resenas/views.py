from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.core.views import get_city
from apps.directorio.models import Oficio
from apps.pedidos import notificaciones as notif
from apps.pedidos.models import Pedido

from .forms import ReclamoForm, ReporteForm, ResenaForm
from .models import Reporte, Resena


def _pedido(token):
    return get_object_or_404(
        Pedido.objects.select_related("city", "barrio", "rubro", "oficio_asignado"), token=token
    )


@require_http_methods(["GET", "POST"])
def resena(request, token):
    pedido = _pedido(token)
    if pedido.estado != Pedido.Estado.COMPLETADO or Resena.objects.filter(pedido=pedido).exists():
        return redirect("pedidos:seguimiento", token=token)
    instancia = Resena(pedido=pedido, oficio=pedido.oficio_asignado)
    if request.method == "POST":
        form = ResenaForm(request.POST, instance=instancia)
        if form.is_valid():
            form.save()
            messages.success(request, "¡Gracias! Tu reseña ya está publicada en la ficha.")
            return redirect("pedidos:seguimiento", token=token)
    else:
        nombre = pedido.nombre_vecino.split()[0] if pedido.nombre_vecino else "Vecino"
        form = ResenaForm(instance=instancia, initial={"autor": f"{nombre}, B° {pedido.barrio.nombre}"})
    return render(request, "resenas/resena.html", {"city": pedido.city, "pedido": pedido, "form": form})


@require_http_methods(["GET", "POST"])
def reclamo(request, token):
    pedido = _pedido(token)
    if not pedido.oficio_asignado:
        return redirect("pedidos:seguimiento", token=token)
    if request.method == "POST":
        form = ReclamoForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                r = form.save(commit=False)
                r.pedido = pedido
                r.save()
                notif.avisar_admin("admin_reclamo", pedido, reclamo=r)
            messages.success(request, "Recibimos tu reclamo. El administrador de la guía lo va a revisar.")
            return redirect("pedidos:seguimiento", token=token)
    else:
        form = ReclamoForm()
    return render(request, "resenas/reclamo.html", {"city": pedido.city, "pedido": pedido, "form": form})


TEXTOS_REPORTE = {
    Reporte.Tipo.RECOMENDACION: (
        "Recomendá un oficio",
        "¿Conocés a alguien que trabaja bien y no está en la guía? Contanos y lo invitamos.",
        "Gracias por recomendar",
        "Lo vamos a contactar para invitarlo. Antes de publicarlo verificamos su identidad.",
    ),
    Reporte.Tipo.ADVERTENCIA: (
        "Advertí sobre un oficio",
        "¿Tuviste un problema con un oficio de la guía, o con alguien que ofrece trabajos? Contanos. No se publica.",
        "Gracias por avisar",
        "El administrador de la guía lo va a revisar. Si un oficio de la guía incumple, se lo suspende.",
    ),
}


@require_http_methods(["GET", "POST"])
def reporte(request, city_slug, tipo):
    city = get_city(city_slug)
    titulo, bajada, titulo_ok, texto_ok = TEXTOS_REPORTE[tipo]
    if request.method == "POST":
        form = ReporteForm(request.POST, tipo=tipo)
        if form.is_valid():
            with transaction.atomic():
                r = form.save(commit=False)
                r.city = city
                r.oficio = Oficio.objects.filter(city=city, nombre__iexact=r.nombre_oficio).first()
                r.save()
                notif.avisar_admin(
                    "admin_reporte", reporte=r,
                    link_admin=settings.SITE_URL + reverse("admin:resenas_reporte_change", args=[r.pk]),
                )
            return render(request, "core/gracias.html", {"city": city, "titulo": titulo_ok, "texto": texto_ok})
    else:
        oficio = Oficio.objects.filter(city=city, slug=request.GET.get("oficio", "")).first()
        form = ReporteForm(tipo=tipo, initial={"nombre_oficio": oficio.nombre if oficio else ""})
    return render(request, "resenas/reporte.html", {
        "city": city, "form": form, "titulo": titulo, "bajada": bajada,
    })
