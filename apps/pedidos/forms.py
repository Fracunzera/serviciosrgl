from datetime import timedelta

from django import forms
from django.conf import settings
from django.utils import timezone

from apps.core.models import Barrio
from apps.directorio.models import Rubro

from .models import Pedido
from .ruteo import detectar_rubro

MAX_FOTOS = 3
MAX_MB_FOTO = 8


class VariosArchivos(forms.ClearableFileInput):
    allow_multiple_selected = True


class FotosField(forms.ImageField):
    """Varias imágenes en un solo input (patrón de la doc de Django)."""

    def __init__(self, *args, max_fotos=MAX_FOTOS, **kwargs):
        self.max_fotos = max_fotos
        kwargs.setdefault("widget", VariosArchivos(attrs={"accept": "image/*"}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        archivos = [a for a in (data if isinstance(data, (list, tuple)) else [data]) if a]
        if len(archivos) > self.max_fotos:
            raise forms.ValidationError(f"Podés subir hasta {self.max_fotos} fotos.")
        limpias = []
        for a in archivos:
            if a.size > MAX_MB_FOTO * 1024 * 1024:
                raise forms.ValidationError(f"Cada foto puede pesar hasta {MAX_MB_FOTO} MB.")
            limpias.append(super().clean(a, initial))
        return limpias


class PedidoForm(forms.ModelForm):
    fotos = FotosField(label="Fotos (opcional, hasta 3)")
    # Honeypot: invisible para personas; los bots lo completan.
    sitio_web = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    class Meta:
        model = Pedido
        fields = ["rubro", "barrio", "descripcion", "nombre_vecino", "telefono_vecino", "email_vecino", "direccion"]
        labels = {
            "descripcion": "¿Qué necesitás?",
            "nombre_vecino": "Tu nombre",
            "telefono_vecino": "Tu teléfono",
            "email_vecino": "Tu mail",
            "direccion": "Dirección",
        }
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 4}),
            "telefono_vecino": forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel"}),
            "email_vecino": forms.EmailInput(attrs={"autocomplete": "email"}),
            "nombre_vecino": forms.TextInput(attrs={"autocomplete": "given-name"}),
        }

    def __init__(self, *args, city, **kwargs):
        self.city = city
        super().__init__(*args, **kwargs)
        self.instance.city = city
        self.fields["barrio"].queryset = Barrio.objects.filter(city=city)
        self.fields["barrio"].empty_label = "Elegí tu barrio"

    def clean_sitio_web(self):
        if self.cleaned_data.get("sitio_web"):
            raise forms.ValidationError("No pudimos procesar el pedido.")
        return ""

    def clean_telefono_vecino(self):
        tel = self.cleaned_data["telefono_vecino"].strip()
        desde = timezone.now() - timedelta(hours=1)
        recientes = Pedido.objects.filter(telefono_vecino=tel, creado_at__gte=desde).count()
        if recientes >= settings.PEDIDOS_POR_TELEFONO_POR_HORA:
            raise forms.ValidationError("Ya hiciste varios pedidos en la última hora. Esperá un rato o seguí los que ya tenés.")
        return tel


class PresupuestoForm(PedidoForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.es_urgencia = False
        self.fields["rubro"].empty_label = "Elegí el rubro"
        self.fields["email_vecino"].required = True
        self.fields["email_vecino"].help_text = "Te avisamos por mail cada vez que un oficio responde."
        self.fields["direccion"].help_text = "Opcional. Solo la ve el oficio que elijas."


class UrgenciaForm(PedidoForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.es_urgencia = True
        self.rubros_urgencia = list(Rubro.objects.filter(en_urgencias=True))
        rubro = self.fields["rubro"]
        rubro.queryset = Rubro.objects.filter(en_urgencias=True)
        rubro.required = False
        rubro.widget = forms.RadioSelect()
        rubro.empty_label = None
        rubro.label = "¿Qué tipo de urgencia?"
        rubro.help_text = "Si no sabés, contalo abajo y lo detectamos."
        self.fields["descripcion"].label = "Contá qué pasa"
        self.fields["email_vecino"].required = False
        self.fields["email_vecino"].label = "Tu mail (opcional)"
        self.fields["direccion"].required = True
        self.fields["direccion"].help_text = "Solo la ve el oficio que acepte la urgencia."

    def clean(self):
        datos = super().clean()
        if not datos.get("rubro") and datos.get("descripcion"):
            detectado = detectar_rubro(datos["descripcion"], self.rubros_urgencia)
            if detectado is None:
                self.add_error("rubro", "No pudimos darnos cuenta del rubro: elegilo de la lista.")
            else:
                datos["rubro"] = detectado
                self.instance.rubro = detectado
        return datos


class RespuestaForm(forms.Form):
    mensaje = forms.CharField(
        label="Tu respuesta al vecino",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Ej.: cuándo podés ir, si necesitás verlo antes, qué incluye.",
        max_length=1000,
    )
    monto = forms.IntegerField(label="Monto estimado en $ (opcional)", required=False, min_value=0)


class DisponibilidadForm(forms.Form):
    hasta = forms.DateField(
        label="No estoy disponible hasta el (inclusive)",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean_hasta(self):
        hasta = self.cleaned_data["hasta"]
        if hasta < timezone.localdate():
            raise forms.ValidationError("Elegí una fecha de hoy en adelante.")
        if hasta > timezone.localdate() + timedelta(days=180):
            raise forms.ValidationError("Como máximo 6 meses.")
        return hasta
