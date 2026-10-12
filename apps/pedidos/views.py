from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.core.imagenes import achicar
from apps.core.views import get_city
from apps.directorio.models import Oficio, Rubro

from . import links, ruteo, telegram
from .forms import DisponibilidadForm, PresupuestoForm, RespuestaForm, UrgenciaForm
from .models import Derivacion, FotoPedido, Pedido
from .whatsapp import link_wa


def _guardar_pedido(form):
    # Las fotos se procesan antes de abrir la transacción: Pillow tarda y no
    # queremos el lock de escritura tomado mientras tanto.
    fotos = [achicar(f, f.name) for f in form.cleaned_data.get("fotos") or []]
    pedido = form.save()
    for f in fotos:
        FotoPedido.objects.create(pedido=pedido, imagen=f)
    return pedido


@require_http_methods(["GET", "POST"])
def presupuesto(request, city_slug):
    city = get_city(city_slug)
    slug_oficio = request.POST.get("oficio") or request.GET.get("oficio")
    directo = Oficio.objects.publicados().filter(city=city, slug=slug_oficio).first() if slug_oficio else None

    if request.method == "POST":
        form = PresupuestoForm(request.POST, request.FILES, city=city)
        if directo:
            form.fields["rubro"].queryset = directo.rubros.all()
        if form.is_valid():
            with transaction.atomic():
                pedido = _guardar_pedido(form)
                ruteo.derivar_presupuesto(pedido, oficio_directo=directo)
            return redirect("pedidos:seguimiento", token=pedido.token)
    else:
        inicial = {}
        if request.GET.get("rubro"):
            inicial["rubro"] = _rubro_por_slug(request.GET["rubro"])
        form = PresupuestoForm(initial=inicial, city=city)
        if directo:
            form.fields["rubro"].queryset = directo.rubros.all()
            if directo.rubros.count() == 1:
                form.initial["rubro"] = directo.rubros.first()
    return render(request, "pedidos/presupuesto.html", {"city": city, "form": form, "directo": directo})


def _rubro_por_slug(slug):
    return Rubro.objects.filter(slug=slug).first()


@require_http_methods(["GET", "POST"])
def urgencia(request, city_slug):
    city = get_city(city_slug)
    if request.method == "POST":
        form = UrgenciaForm(request.POST, request.FILES, city=city)
        if form.is_valid():
            with transaction.atomic():
                pedido = _guardar_pedido(form)
                ruteo.escalar(pedido.pk)
            return redirect("pedidos:seguimiento", token=pedido.token)
    else:
        form = UrgenciaForm(initial={"rubro": _rubro_por_slug(request.GET.get("rubro", ""))}, city=city)
    return render(request, "pedidos/urgencia.html", {
        "city": city, "form": form, "minutos": settings.URGENCIA_MINUTOS_PARA_ACEPTAR,
    })


# --- Vecino: link de seguimiento -----------------------------------------------


@require_http_methods(["GET", "POST"])
def seguimiento(request, token):
    pedido = get_object_or_404(
        Pedido.objects.select_related("city", "rubro", "barrio", "oficio_asignado"), token=token
    )
    if request.method == "POST":
        accion = request.POST.get("accion")
        ok = False
        if accion == "elegir":
            ok = ruteo.elegir(pedido, request.POST.get("derivacion"))
            msg = "¡Listo! Ya tenés el contacto y le avisamos que lo elegiste."
        elif accion == "confirmar":
            ok = ruteo.confirmar_trabajo(pedido)
            msg = "Gracias por confirmar. Contanos cómo te fue."
        elif accion == "cancelar":
            ok = ruteo.cancelar(pedido)
            msg = "Cancelaste el pedido."
        if ok:
            messages.success(request, msg)
        else:
            messages.error(request, "No se pudo: el pedido cambió de estado. Mirá cómo está ahora.")
        return redirect("pedidos:seguimiento", token=pedido.token)

    respuestas = pedido.derivaciones.filter(
        estado__in=[Derivacion.Estado.RESPONDIDA, Derivacion.Estado.ELEGIDA]
    ).select_related("oficio").order_by("respondido_at")
    asignado = pedido.oficio_asignado
    contexto = {
        "city": pedido.city,
        "pedido": pedido,
        "respuestas": respuestas,
        "avisados": pedido.derivaciones.count(),
        "fotos": pedido.fotos.all(),
        "wa_oficio": asignado and link_wa(
            asignado.telefono, f"Hola {asignado.nombre}, te escribo por el pedido de Servicios RGL.",
            pedido.city.codigo_area,
        ),
        "resena": getattr(pedido, "resena", None) if pedido.estado == Pedido.Estado.COMPLETADO else None,
    }
    if pedido.es_urgencia and pedido.fallback_at and pedido.estado == Pedido.Estado.ABIERTO:
        contexto["fallback"] = _lista_fallback(pedido)
    return render(request, "pedidos/seguimiento.html", contexto)


def _lista_fallback(pedido):
    """Oficios de guardia del rubro que dieron consentimiento para mostrar su
    teléfono. Primero los que están de turno ahora."""
    ahora = timezone.now()
    oficios = [
        o for o in Oficio.objects.publicados()
        .filter(city=pedido.city, rubros=pedido.rubro, de_guardia=True, telefono_en_fallback=True)
        .select_related("city").prefetch_related("franjas")
        if o.disponible(ahora)
    ]
    oficios.sort(key=lambda o: (not o.de_guardia_en(ahora), o.nombre))
    return [(o, link_wa(o.telefono, "", pedido.city.codigo_area)) for o in oficios]


# --- Oficio: links firmados ----------------------------------------------------


def _link_invalido(request):
    return render(request, "pedidos/link_invalido.html", status=404)


@require_http_methods(["GET", "POST"])
def derivacion(request, firma):
    d = links.leer_derivacion(firma)
    if d is None:
        return _link_invalido(request)
    pedido, oficio = d.pedido, d.oficio
    activo = oficio.estado == Oficio.Estado.APROBADO
    form = RespuestaForm()

    # GET nunca cambia nada: los antivirus de mail abren los links solos.
    if request.method == "POST" and activo:
        accion = request.POST.get("accion")
        if accion == "aceptar" and pedido.es_urgencia:
            if ruteo.aceptar_urgencia(d):
                messages.success(request, "¡Es tuyo! Abajo tenés el contacto del vecino.")
            else:
                messages.error(request, "Llegaste tarde: ya lo tomó otro oficio. ¡Gracias igual!")
            return redirect(request.path)
        if accion == "rechazar":
            ruteo.rechazar(d)
            messages.info(request, "Listo, se lo pasamos a otro oficio.")
            return redirect(request.path)
        if accion == "responder" and not pedido.es_urgencia:
            form = RespuestaForm(request.POST)
            if form.is_valid():
                if ruteo.responder_presupuesto(d, form.cleaned_data["mensaje"], form.cleaned_data["monto"]):
                    messages.success(request, "Le mandamos tu respuesta al vecino. Si te elige, te avisamos.")
                else:
                    messages.error(request, "El pedido ya no está abierto.")
                return redirect(request.path)

    d.refresh_from_db()
    pedido.refresh_from_db()
    es_mio = pedido.oficio_asignado_id == oficio.pk
    abierto = pedido.estado == Pedido.Estado.ABIERTO
    return render(request, "pedidos/derivacion.html", {
        "city": pedido.city,
        "d": d,
        "pedido": pedido,
        "oficio": oficio,
        "activo": activo,
        "form": form,
        "fotos": pedido.fotos.all(),
        "es_mio": es_mio,
        "puede_aceptar": activo and abierto and pedido.es_urgencia
        and d.estado in (Derivacion.Estado.ENVIADA, Derivacion.Estado.VENCIDA),
        "puede_responder": activo and abierto and not pedido.es_urgencia and d.estado == Derivacion.Estado.ENVIADA,
        "wa_vecino": es_mio and link_wa(
            pedido.telefono_vecino, f"Hola {pedido.nombre_vecino}, soy {oficio.nombre}, de Servicios RGL.",
            pedido.city.codigo_area,
        ),
        "link_disponibilidad": links.link_disponibilidad(oficio),
    })


@require_http_methods(["GET", "POST"])
def disponibilidad(request, firma):
    oficio = links.leer_oficio(firma)
    if oficio is None:
        return _link_invalido(request)
    form = DisponibilidadForm()
    if request.method == "POST":
        if request.POST.get("accion") == "desvincular_telegram":
            Oficio.objects.filter(pk=oficio.pk).update(telegram_chat_id="")
            messages.success(request, "Listo: Telegram desvinculado. Los avisos te llegan por mail.")
            return redirect(request.path)
        if request.POST.get("accion") == "disponible":
            oficio.no_disponible_hasta = None
            oficio.save(update_fields=["no_disponible_hasta", "actualizado_at"])
            messages.success(request, "Listo: estás disponible otra vez.")
            return redirect(request.path)
        form = DisponibilidadForm(request.POST)
        if form.is_valid():
            # Hasta el final del día elegido, en la hora de la ciudad.
            tz = ZoneInfo(oficio.city.timezone)
            dia_siguiente = form.cleaned_data["hasta"] + timedelta(days=1)
            oficio.no_disponible_hasta = datetime.combine(dia_siguiente, time(0), tzinfo=tz)
            oficio.save(update_fields=["no_disponible_hasta", "actualizado_at"])
            messages.success(request, "Listo: hasta esa fecha no te llegan pedidos.")
            return redirect(request.path)
    return render(request, "pedidos/disponibilidad.html", {
        "city": oficio.city,
        "oficio": oficio,
        "form": form,
        "ausente": not oficio.disponible(),
        "link_telegram": telegram.link_vinculacion(oficio),
    })
