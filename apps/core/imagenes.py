"""Fotos subidas: se achican y se re-codifican como JPEG.

Re-codificar descarta el EXIF, que en fotos de celular trae la ubicación
GPS de la casa del vecino.
"""

from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

LADO_MAXIMO = 1600


def achicar(archivo, nombre="foto.jpg"):
    with Image.open(archivo) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
        salida = BytesIO()
        im.save(salida, "JPEG", quality=82, optimize=True)
    return ContentFile(salida.getvalue(), name=nombre.rsplit(".", 1)[0] + ".jpg")
