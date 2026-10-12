"""
Backup en caliente de la base: usa la API de backup de SQLite, que es
consistente aunque web y worker estén escribiendo (copiar el archivo con cp
no lo es: puede quedar afuera lo que todavía está en el -wal).

Deja DATA_DIR/respaldos/db-AAAAMMDD-HHMM.sqlite3 y conserva los últimos N.
Las fotos (DATA_DIR/media) se respaldan copiando la carpeta: son archivos
que no cambian una vez escritos.
"""

import sqlite3

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Backup consistente de la base SQLite en DATA_DIR/respaldos/."

    def add_arguments(self, parser):
        parser.add_argument("--conservar", type=int, default=14, help="Cuántos respaldos guardar (default 14).")

    def handle(self, *args, conservar, **options):
        carpeta = settings.DATA_DIR / "respaldos"
        carpeta.mkdir(exist_ok=True)
        destino = carpeta / f"db-{timezone.localtime():%Y%m%d-%H%M}.sqlite3"
        origen = sqlite3.connect(settings.DATABASES["default"]["NAME"])
        copia = sqlite3.connect(destino)
        try:
            with copia:
                origen.backup(copia)
            ok = copia.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            copia.close()
            origen.close()
        if ok != "ok":
            raise SystemExit(f"El respaldo {destino} no pasó integrity_check: {ok}")

        viejos = sorted(carpeta.glob("db-*.sqlite3"))[:-conservar] if conservar > 0 else []
        for v in viejos:
            v.unlink()
        self.stdout.write(f"Respaldo: {destino} ({destino.stat().st_size // 1024} KB). Borrados: {len(viejos)}.")
