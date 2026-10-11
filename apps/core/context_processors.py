from django.conf import settings
from django.utils import timezone


def guia(request):
    return {
        "EDICION": timezone.localdate().year,
        "FACEBOOK_GRUPO_URL": settings.FACEBOOK_GRUPO_URL,
    }
