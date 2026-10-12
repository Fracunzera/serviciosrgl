from datetime import time

from django import forms

from apps.core.models import Barrio
from apps.pedidos.forms import FotosField

from .models import Oficio, Rubro

# Textos exactos que acepta el oficio. Se guardan en cada Consentimiento:
# si cambia la redacción, los registros viejos conservan lo que se aceptó.
TEXTO_TERMINOS = (
    "Leí y acepto los Términos y el Aviso de privacidad de Servicios RGL. "
    "Entiendo que la guía conecta vecinos con oficios y no intermedia en el trabajo ni en el pago."
)
TEXTO_FALLBACK = (
    "Acepto que mi teléfono se muestre en la lista de urgencias cuando ningún oficio "
    "de guardia acepte el pedido a tiempo. Puedo revocarlo cuando quiera."
)

# Franjas típicas: lo fino se ajusta desde el admin.
FRANJAS = {
    "vie_noche": ("Viernes de 20 a sábado 8", [(4, time(20), time(8))]),
    "sab": ("Sábado de 8 a 22", [(5, time(8), time(22))]),
    "sab_noche": ("Sábado de 22 a domingo 8", [(5, time(22), time(8))]),
    "dom": ("Domingo de 8 a 22", [(6, time(8), time(22))]),
    "dom_noche": ("Domingo de 22 a lunes 8", [(6, time(22), time(8))]),
    "semana_noche": ("Noches de semana (lun a jue, de 20 a 8)", [(d, time(20), time(8)) for d in range(4)]),
}


class AltaOficioForm(forms.ModelForm):
    rubros = forms.ModelMultipleChoiceField(
        queryset=Rubro.objects.all(), widget=forms.CheckboxSelectMultiple, label="Rubros"
    )
    barrios = forms.ModelMultipleChoiceField(
        queryset=Barrio.objects.none(), widget=forms.CheckboxSelectMultiple, required=False,
        label="Barrios donde trabajás",
    )
    franjas = forms.MultipleChoiceField(
        choices=[(k, v[0]) for k, v in FRANJAS.items()], widget=forms.CheckboxSelectMultiple,
        required=False, label="Guardias de urgencia (opcional)",
        help_text="En estos horarios te pueden llegar urgencias. Si no marcás nada, solo recibís pedidos de presupuesto.",
    )
    fotos = FotosField(max_fotos=6, label="Fotos de trabajos (opcional, hasta 6)")
    acepto_terminos = forms.BooleanField(label=TEXTO_TERMINOS)
    consiento_fallback = forms.BooleanField(required=False, label=TEXTO_FALLBACK)
    sitio_web = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    class Meta:
        model = Oficio
        fields = ["nombre", "rubros", "cubre_toda_la_ciudad", "barrios", "anios_oficio", "garantia",
                  "descripcion", "matricula", "telefono", "email"]
        labels = {
            "nombre": "Nombre y apellido, o nombre del comercio",
            "cubre_toda_la_ciudad": "Trabajo en toda la ciudad",
            "descripcion": "Contá qué hacés",
            "telefono": "Teléfono",
            "email": "Mail",
        }
        help_texts = {
            "matricula": "Obligatoria para gas y electricidad. La vamos a controlar.",
            "telefono": "No se publica. Lo usamos para verificarte y, si aceptás abajo, para las urgencias.",
            "email": "Por acá te llegan los pedidos.",
        }
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 4}), "cubre_toda_la_ciudad": forms.CheckboxInput}

    def __init__(self, *args, city, **kwargs):
        self.city = city
        super().__init__(*args, **kwargs)
        self.instance.city = city
        self.fields["barrios"].queryset = Barrio.objects.filter(city=city)

    def clean_sitio_web(self):
        if self.cleaned_data.get("sitio_web"):
            raise forms.ValidationError("No pudimos procesar la solicitud.")
        return ""

    def clean(self):
        datos = super().clean()
        exigen = [r.nombre for r in datos.get("rubros") or [] if r.requiere_matricula]
        if exigen and not datos.get("matricula"):
            self.add_error("matricula", f"Para {', '.join(exigen)} la matrícula es obligatoria.")
        if not datos.get("cubre_toda_la_ciudad") and not datos.get("barrios"):
            self.add_error("barrios", "Marcá los barrios donde trabajás, o «Trabajo en toda la ciudad».")
        if datos.get("consiento_fallback") and not datos.get("franjas"):
            self.add_error("consiento_fallback", "Solo aplica si hacés guardias de urgencia.")
        return datos
