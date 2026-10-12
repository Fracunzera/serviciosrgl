from django import forms

from .models import Reclamo, Reporte, Resena


class ResenaForm(forms.ModelForm):
    estrellas = forms.TypedChoiceField(
        label="¿Cómo fue el trabajo?",
        choices=[(n, "★" * n) for n in range(5, 0, -1)],
        coerce=int,
        widget=forms.RadioSelect,
    )

    class Meta:
        model = Resena
        fields = ["estrellas", "texto", "autor"]
        labels = {"texto": "Contale a otros vecinos (opcional)", "autor": "Cómo firmás"}
        help_texts = {"autor": "Se publica así. No pongas tu apellido completo si no querés."}
        widgets = {"texto": forms.Textarea(attrs={"rows": 4})}


class ReclamoForm(forms.ModelForm):
    class Meta:
        model = Reclamo
        fields = ["texto"]
        labels = {"texto": "¿Qué pasó?"}
        help_texts = {"texto": "Le llega al administrador de la guía. Si el oficio incumplió, puede ser suspendido."}
        widgets = {"texto": forms.Textarea(attrs={"rows": 5})}


class ReporteForm(forms.ModelForm):
    sitio_web = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    class Meta:
        model = Reporte
        fields = ["nombre_oficio", "rubro", "telefono_oficio", "texto", "contacto"]
        labels = {
            "nombre_oficio": "Nombre del oficio",
            "rubro": "Rubro",
            "telefono_oficio": "Su teléfono (si lo tenés)",
            "contacto": "Tu contacto (opcional)",
        }
        widgets = {"texto": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, tipo, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.tipo = tipo
        if tipo == Reporte.Tipo.RECOMENDACION:
            self.fields["texto"].label = "¿Por qué lo recomendás?"
        else:
            self.fields["texto"].label = "¿Qué pasó?"
            self.fields["texto"].help_text = "Contá hechos concretos. No se publica: lo revisa el administrador."
            del self.fields["telefono_oficio"]

    def clean_sitio_web(self):
        if self.cleaned_data.get("sitio_web"):
            raise forms.ValidationError("No pudimos procesar el envío.")
        return ""
