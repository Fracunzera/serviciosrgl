from django import template

register = template.Library()


@register.filter
def estrellas(valor):
    """4.6 → "★★★★★", 3.2 → "★★★☆☆" (redondeo al entero más cercano)."""
    try:
        n = max(0, min(5, round(float(valor))))
    except (TypeError, ValueError):
        return ""
    return "★" * n + "☆" * (5 - n)
