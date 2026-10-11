# Servicios RGL

Directorio de oficios de Río Gallegos con pedidos de presupuesto y línea de
urgencias de fin de semana. Decisiones y arquitectura: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## Local

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DJANGO_DEBUG=1
.venv/bin/python manage.py migrate
.venv/bin/python manage.py cargar_demo      # ciudad, barrios, rubros + oficios inventados
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver        # web
.venv/bin/python manage.py worker           # en otra terminal
```

Tests: `DJANGO_DEBUG=1 .venv/bin/python manage.py test`.

Con `DJANGO_DEBUG=1` los mails salen por consola y Django sirve `/media/`.

## Producción (VPS)

```sh
cp .env.example .env                 # completar
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
docker compose exec web python manage.py cargar_base   # ciudad, barrios y rubros reales
docker compose exec web python manage.py createsuperuser
```

- La web escucha en `127.0.0.1:8000`. Adelante va el reverse proxy, que se encarga de TLS y de la redirección a https, y además sirve `/media/` desde `./data/media`.
- Para el backup en caliente: `sqlite3 data/db.sqlite3 ".backup /ruta/backup.sqlite3"` más una copia de `data/media/`.
