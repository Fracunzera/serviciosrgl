import re
from urllib.parse import quote


def numero_wa(telefono, codigo_area="2966"):
    """Teléfono argentino como se escribe acá → formato wa.me (549 + área + número).

    "2966 15-123456" → "5492966123456"; "15 123456" → "5492966123456";
    "+54 9 2966 123456" → "5492966123456". Devuelve "" si no se puede.
    """
    d = re.sub(r"\D", "", telefono or "")
    if d.startswith("54"):
        d = d[2:]
        if d.startswith("9"):
            d = d[1:]
    d = d.lstrip("0")
    if d.startswith(codigo_area):
        d = d[len(codigo_area):]
        if d.startswith("15"):
            d = d[2:]
        d = codigo_area + d
    elif d.startswith("15") and len(d) == 8:  # "15 123456" local
        d = codigo_area + d[2:]
    elif len(d) == 6:  # "123456" local
        d = codigo_area + d
    return "549" + d if len(d) == 10 else ""


def link_wa(telefono, texto="", codigo_area="2966"):
    n = numero_wa(telefono, codigo_area)
    if not n:
        return ""
    return f"https://wa.me/{n}" + (f"?text={quote(texto)}" if texto else "")
