# Deploy y operación

Checklist para poner la guía en un VPS y mantenerla con el menor trabajo
posible. Se asume Docker + Compose en el host y un dominio apuntando al VPS.

---

## 1. Servidor

- **Disco local para `./data`.** SQLite en modo WAL necesita locking real: NFS, SMB o volúmenes de red no sirven.
- **Usuario de los contenedores:** corren con uid 1000, así que la carpeta de datos tiene que ser de ese uid:

  ```sh
  git clone <repo> /srv/serviciosrgl && cd /srv/serviciosrgl
  mkdir -p data && sudo chown 1000:1000 data
  ```

## 2. Variables (`.env`)

```sh
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(50))'   # → DJANGO_SECRET_KEY
```

| Variable | Valor |
|---|---|
| `DJANGO_DEBUG` | `0` |
| `DJANGO_SECRET_KEY` | La generada arriba. Si algún día la rotás, la vieja va a `DJANGO_SECRET_KEY_FALLBACKS`, así no se invalidan los links que ya mandaste. |
| `DJANGO_ALLOWED_HOSTS` | `tudominio.com.ar` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://tudominio.com.ar` |
| `SITE_URL` | `https://tudominio.com.ar`. Es obligatoria: con ella se arman los links de los mails. |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | Datos SMTP del proveedor de mail (ver §5). |
| `ADMIN_EMAIL` | Tu mail. Te llegan las urgencias sin cubrir, los reclamos, las altas y las copias semimanuales. |
| `TITULAR_NOMBRE`, `TITULAR_DOMICILIO`, `CONTACTO_EMAIL` | Aparecen en Términos y Privacidad. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` | Ver §6. Si quedan vacías, Telegram está apagado. |
| `FACEBOOK_GRUPO_URL` | Link al grupo de vecinos. |
| `SERVE_MEDIA` | `0` si el proxy sirve `/media/`, que es lo recomendado. |

## 3. Reverse proxy

La web escucha en `127.0.0.1:8000`. El proxy se encarga de tres cosas:

1. Terminar TLS.
2. Redirigir http → https.
3. Servir `/media/` directo desde el disco.

Además, tiene que **juntar todo el cuerpo de la subida antes de pasárselo a gunicorn**. Así un celular con mala señal subiendo fotos no ocupa un worker durante todo el envío. Hay que permitir hasta unos 60 MB: el alta de oficio acepta 6 fotos de hasta 8 MB cada una.

### Caddy (más simple: TLS automático)

```caddy
tudominio.com.ar {
	encode zstd gzip

	handle_path /media/* {
		root * /srv/serviciosrgl/data/media
		file_server
	}

	handle {
		request_body {
			max_size 60MB
		}
		reverse_proxy 127.0.0.1:8000 {
			request_buffers 60MB
		}
	}
}
```

### nginx

```nginx
server {
    listen 80;
    server_name tudominio.com.ar;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name tudominio.com.ar;
    # ssl_certificate / ssl_certificate_key: certbot

    client_max_body_size 60m;          # nginx ya bufferea el body por defecto

    location /media/ {
        alias /srv/serviciosrgl/data/media/;
        add_header X-Content-Type-Options nosniff;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;   # Django lo usa para saber que es https
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Los nombres de las fotos son aleatorios (uuid). Igual, asegurate de que el listado de directorios esté apagado: es el default en nginx y en Caddy con `file_server` sin `browse`.

## 4. Primer arranque

```sh
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py cargar_base       # ciudad, barrios y rubros reales
```

**Nunca corras `cargar_demo` en producción**: carga oficios inventados. Sin `--forzar` se niega a correr igual.

Después, entrá a `/admin/` y revisá:

- **Ciudades → Río Gallegos:** completá y corregí los barrios. La lista inicial es aproximada.
- **Rubros:** las palabras clave que detectan el rubro en urgencias, y el "Ver también".

## 5. Mail

Un proveedor SMTP transaccional. Varios tienen plan gratis de cientos de mails por día, que sobra para este volumen; revisá las condiciones actuales.

- **Registros DNS del dominio que manda.** Sin ellos, los avisos a los oficios caen en spam:
  - **SPF:** `TXT @ "v=spf1 include:<lo que indique el proveedor> -all"`
  - **DKIM:** el registro que te da el proveedor, normalmente un `CNAME` o `TXT`.
  - **DMARC:** arrancá con `TXT _dmarc "v=DMARC1; p=none; rua=mailto:<tu mail>"`.
- **Probarlo:**

  ```sh
  docker compose exec web python manage.py sendtestemail tu@mail.com
  ```

  Mandá también un aviso real a un servicio de "puntaje de spam" y apuntá a 9/10 o más.

## 6. Telegram (recomendado: le ahorra trabajo al modo semimanual)

1. En Telegram, hablale a **@BotFather** → `/newbot` → elegí nombre y usuario (ej. `ServiciosRGLbot`).
2. Copiá el token a `TELEGRAM_BOT_TOKEN` y el usuario, sin `@`, a `TELEGRAM_BOT_USERNAME`.
3. Recreá los contenedores con `docker compose up -d`. Hace falta `up`, no `restart`: `restart` no relee el `.env`.
4. **Probalo:** creá un oficio de prueba, mandale el link de disponibilidad desde el admin ("Mandar link de disponibilidad"), abrí el link "Vincular Telegram" desde el celular y tocá «Iniciar». El bot tiene que contestar "¡Listo…!".

El worker lee el bot por polling, así que no hay webhook que configurar. No le configures un webhook al bot desde otro lado: Telegram no deja usar las dos cosas a la vez.

## 7. Backups

**Diario, con cron en el host:**

```cron
# 03:30: backup consistente de la base + copia fuera del VPS
30 3 * * * cd /srv/serviciosrgl && docker compose exec -T web python manage.py respaldar --conservar 14 && rsync -a data/respaldos data/media backup@otro-host:serviciosrgl/
```

- `respaldar` usa la API de backup de SQLite: es consistente aunque web y worker estén escribiendo. **No uses `cp` sobre `db.sqlite3` en caliente**: puede quedar afuera lo que todavía está en el `-wal`.
- Las fotos (`data/media`) se copian con `rsync`: no cambian una vez escritas.

**Restaurar.** Probalo una vez antes de necesitarlo:

```sh
docker compose down
cp data/respaldos/db-AAAAMMDD-HHMM.sqlite3 data/db.sqlite3
rm -f data/db.sqlite3-wal data/db.sqlite3-shm
docker compose up -d
```

## 8. Monitoreo (mínimo)

- **Un servicio externo de uptime** que pegue a `https://tudominio.com.ar/healthz` y te avise si cae.
- **El tablero "Para revisar" del admin.** Si "Mensajes trabados" no está en 0, el worker no está corriendo. Revisalo con `docker compose ps` y `docker compose logs worker`.

## 9. Actualizar

```sh
git pull
docker compose up -d --build      # web aplica migraciones al arrancar; el worker espera a web
```

- **Seguridad de Django.** Django 5.2 es LTS con parches hasta abril de 2028. Una vez por mes, revisá si salió un 5.2.x nuevo, actualizalo en `requirements.txt` y reconstruí.
- **Cuando salga la 6.2 LTS**, planificá el salto.

## 10. Antes de anunciarla

- [ ] **`.env` completo**, con `DJANGO_DEBUG=0` y la `SECRET_KEY` generada.
- [ ] **`check --deploy` limpio:**

  ```sh
  docker compose exec web python manage.py check --deploy
  ```

  Solo deberían quedar los avisos de HSTS.
- [ ] **Textos legales:**
  - Completar los `[COMPLETAR…]` de `apps/core/templates/core/terminos.html` y `privacidad.html`: fecha, jurisdicción, proveedor de mail y número de registro.
  - **Inscribir la base de datos** en el Registro Nacional de Bases de Datos (AAIP) y poner el número.
  - **Que los lea un abogado.**
- [ ] **Barrios y palabras clave** de rubros revisados.
- [ ] **Oficios reales cargados.** Lo ideal es que se anoten ellos por `/anotate/`, o que lo completes con ellos al lado: así queda registrado su consentimiento para mostrar el teléfono en el fallback. Un oficio creado a mano en el admin no tiene ese consentimiento y no aparece en el fallback. Después, aprobalos con la acción "Aprobar".
- [ ] **Reseñas de arranque** cargadas, con la fecha real de cada trabajo.
- [ ] **Mail:** `sendtestemail` OK, SPF, DKIM y DMARC configurados, puntaje de spam alto.
- [ ] **Telegram:** al menos un oficio vinculado y probado.
- [ ] **Prueba de punta a punta:** una urgencia de verdad con un oficio amigo, un sábado. Que acepte, que vos veas el contacto, que confirmes y dejes la reseña.
- [ ] **Backup:** el cron funcionando y una restauración probada.
- [ ] **Monitor externo** apuntando a `/healthz`.
- [ ] `FACEBOOK_GRUPO_URL` cargado.
- [ ] **HSTS:** unas semanas después, con todo estable, subir `SECURE_HSTS_SECONDS=31536000`.
