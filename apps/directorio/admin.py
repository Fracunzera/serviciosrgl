from django import forms
from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html

from . import acciones
from .models import Consentimiento, FotoTrabajo, FranjaGuardia, Oficio, Rubro


@admin.register(Rubro)
class RubroAdmin(admin.ModelAdmin):
    list_display = ["nombre", "requiere_matricula", "en_urgencias", "orden"]
    list_editable = ["requiere_matricula", "en_urgencias", "orden"]
    prepopulated_fields = {"slug": ["nombre"]}
    filter_horizontal = ["ver_tambien"]
    search_fields = ["nombre"]


class FranjaInline(admin.TabularInline):
    model = FranjaGuardia
    extra = 0


class FotoInline(admin.TabularInline):
    model = FotoTrabajo
    extra = 0
    fields = ["vista", "imagen", "descripcion", "orden"]
    readonly_fields = ["vista"]

    @admin.display(description="")
    def vista(self, obj):
        return format_html('<img src="{}" style="height:60px">', obj.imagen.url) if obj.pk else ""


class ConsentimientoInline(admin.TabularInline):
    """Historial legal: solo lectura. Se registra desde el formulario de alta."""
    model = Consentimiento
    extra = 0
    fields = ["creado_at", "tipo", "otorgado", "texto"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class OficioAdminForm(forms.ModelForm):
    class Meta:
        model = Oficio
        fields = "__all__"

    def clean(self):
        datos = super().clean()
        rubros = datos.get("rubros")
        # La M2M recién se puede chequear acá (el modelo no la ve al guardar).
        if self.instance.estado == Oficio.Estado.APROBADO and rubros is not None:
            exigen = [r.nombre for r in rubros if r.requiere_matricula]
            if exigen and not datos.get("matricula"):
                self.add_error("matricula", f"Obligatoria para {', '.join(exigen)}.")
        if not datos.get("cubre_toda_la_ciudad") and not datos.get("barrios"):
            self.add_error("barrios", "Elegí barrios o marcá que cubre toda la ciudad.")
        return datos


class SuspenderForm(forms.Form):
    motivo = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4, "cols": 60}),
        help_text="Queda asentado en el perfil. No se publica.",
    )


@admin.register(Oficio)
class OficioAdmin(admin.ModelAdmin):
    form = OficioAdminForm
    list_display = ["nombre", "lista_rubros", "estado", "es_verificado", "de_guardia",
                    "telefono_en_fallback", "con_telegram", "disponible_ahora", "ultima_asignacion"]
    list_filter = ["estado", "rubros", "de_guardia", "telefono_en_fallback", "city"]
    search_fields = ["nombre", "nombre_guia", "telefono", "email", "matricula"]
    filter_horizontal = ["rubros", "barrios"]
    inlines = [FranjaInline, FotoInline, ConsentimientoInline]
    actions = ["aprobar", "suspender", "revocar_links", "enviar_link_disponibilidad"]
    readonly_fields = ["estado", "ficha", "aprobado_at", "dni_verificado_at", "suspendido_at",
                       "motivo_suspension", "ultima_asignacion", "telefono_en_fallback", "telegram_chat_id",
                       "version_links", "creado_at", "actualizado_at"]
    fieldsets = [
        (None, {"fields": ["city", "nombre", "nombre_guia", "slug", "ficha", "rubros", "descripcion"]}),
        ("Estado", {"fields": ["estado", "aprobado_at", "dni_verificado_at", "suspendido_at", "motivo_suspension"],
                    "description": "Se cambia con las acciones de la lista: Aprobar / Suspender."}),
        ("Zona y oficio", {"fields": ["cubre_toda_la_ciudad", "barrios", "anios_oficio", "garantia", "matricula"]}),
        ("Contacto (privado)", {"fields": ["telefono", "email", "telegram_chat_id"]}),
        ("Guardia", {"fields": ["de_guardia", "no_disponible_hasta", "ultima_asignacion", "telefono_en_fallback"]}),
        ("Sistema", {"fields": ["version_links", "creado_at", "actualizado_at"], "classes": ["collapse"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("rubros")

    @admin.display(description="Rubros")
    def lista_rubros(self, obj):
        return ", ".join(r.nombre for r in obj.rubros.all())

    @admin.display(description="Verificado", boolean=True)
    def es_verificado(self, obj):
        return obj.verificado

    @admin.display(description="Telegram", boolean=True)
    def con_telegram(self, obj):
        return bool(obj.telegram_chat_id)

    @admin.display(description="Disponible", boolean=True)
    def disponible_ahora(self, obj):
        return obj.disponible()

    @admin.display(description="Ficha pública")
    def ficha(self, obj):
        if not obj.pk or obj.estado == Oficio.Estado.PENDIENTE:
            return "—"
        url = reverse("directorio:oficio", args=[obj.city.slug, obj.slug])
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.action(description="Aprobar (DNI verificado)")
    def aprobar(self, request, queryset):
        ok = 0
        for oficio in queryset:
            try:
                acciones.aprobar(oficio)
                ok += 1
            except acciones.NoSePuede as e:
                self.message_user(request, str(e), messages.ERROR)
        if ok:
            self.message_user(request, f"{ok} aprobado(s). Les mandamos el mail de bienvenida.", messages.SUCCESS)

    @admin.action(description="Suspender (con motivo)…")
    def suspender(self, request, queryset):
        """Acción con página intermedia para pedir el motivo."""
        if "aplicar" in request.POST:
            form = SuspenderForm(request.POST)
            if form.is_valid():
                for oficio in queryset:
                    acciones.suspender(oficio, form.cleaned_data["motivo"])
                self.message_user(
                    request,
                    f"{queryset.count()} suspendido(s). Salieron de la guía, sus links quedaron revocados "
                    "y sus pedidos pendientes pasaron a otros oficios.",
                    messages.WARNING,
                )
                return None
        else:
            form = SuspenderForm()
        return TemplateResponse(request, "admin/directorio/oficio/suspender.html", {
            **self.admin_site.each_context(request),
            "title": "Suspender oficios",
            "oficios": queryset,
            "form": form,
            "opts": self.model._meta,
            "accion": "suspender",
            "seleccion": request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
        })

    @admin.action(description="Revocar sus links (si reenviaron un mail a quien no debían)")
    def revocar_links(self, request, queryset):
        for oficio in queryset:
            acciones.revocar_links(oficio)
        self.message_user(request, "Links revocados. Usá «Mandar link de disponibilidad» para darles uno nuevo.")

    @admin.action(description="Mandar link de disponibilidad por mail")
    def enviar_link_disponibilidad(self, request, queryset):
        for oficio in queryset:
            acciones.enviar_link_disponibilidad(oficio)
        self.message_user(request, f"Encolados {queryset.count()} mail(s).")
