"""
Loop del worker: cada N segundos escala urgencias vencidas (o dispara el
fallback), completa pedidos que el vecino no confirmó, cierra pedidos
viejos y despacha la bandeja de salida.

Todo el estado vive en la base, así que matar y reiniciar el contenedor es
seguro: el próximo tick retoma donde quedó.
"""

import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.pedidos import notificaciones, privacidad, ruteo

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Loop de escalado de urgencias."

    def add_arguments(self, parser):
        parser.add_argument(
            "--una-vez", action="store_true", help="Un solo tick y salir (para cron o tests)."
        )

    def handle(self, *args, una_vez=False, **options):
        self.seguir = True
        signal.signal(signal.SIGTERM, self._parar)
        signal.signal(signal.SIGINT, self._parar)

        intervalo = settings.WORKER_INTERVALO_SEGUNDOS
        log.info("worker arrancó (intervalo %ss)", intervalo)
        while self.seguir:
            close_old_connections()
            try:
                self.tick()
            except Exception:
                # Un pedido roto no puede tirar abajo el loop.
                log.exception("error en tick del worker")
            if una_vez:
                break
            # sleep cortado en pasos para responder rápido al SIGTERM.
            for _ in range(intervalo):
                if not self.seguir:
                    break
                time.sleep(1)
        log.info("worker detenido")

    def tick(self):
        # Primero el ruteo (puede encolar avisos), después el envío.
        escaladas = ruteo.procesar_urgencias()
        completados = ruteo.autocompletar()
        cerrados = ruteo.cerrar_viejos()
        privacidad.anonimizar_viejos()
        enviados = notificaciones.enviar_pendientes()
        if escaladas or completados or cerrados or enviados:
            log.info(
                "tick: %s escaladas, %s autocompletados, %s cerrados, %s mensajes enviados",
                escaladas, completados, cerrados, enviados,
            )

    def _parar(self, signum, frame):
        self.seguir = False
