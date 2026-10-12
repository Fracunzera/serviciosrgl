from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.pedidos import notificaciones as notif
from apps.pedidos.models import Pedido

from .forms import ReclamoForm, ResenaForm
from .models import Resena


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
