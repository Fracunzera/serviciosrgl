from django import forms

from .models import Reclamo, Resena


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
