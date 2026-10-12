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

Checklist completo (proxy, mail, Telegram, backups, antes de publicar): [docs/DEPLOY.md](docs/DEPLOY.md).

```sh
cp .env.example .env                 # completar
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
docker compose exec web python manage.py cargar_base   # ciudad, barrios y rubros reales
docker compose exec web python manage.py createsuperuser
```

- La web escucha en `127.0.0.1:8000`. Adelante va el reverse proxy, que se encarga de TLS y de la redirección a https, y además sirve `/media/` desde `./data/media`.
- Completá `TITULAR_NOMBRE`, `TITULAR_DOMICILIO` y `CONTACTO_EMAIL` en `.env`, porque aparecen en Términos y Privacidad. Además, buscá `[COMPLETAR` en `apps/core/templates/core/` para los datos que faltan (fecha, número de registro de la base de datos, proveedor de mail). Antes de publicar, los textos legales tienen que pasar por un abogado.
- Backup en caliente: `docker compose exec -T web python manage.py respaldar` (deja `data/respaldos/`) más una copia de `data/media/`.
