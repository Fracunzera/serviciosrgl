# Arquitectura y decisiones — Servicios RGL

Documento vivo. Cada decisión lleva su "por qué", para poder revisarla más adelante.

## Principio rector

El costo del proyecto es el tiempo del admin, no el hosting. Entre dos
opciones, gana la que tiene menos partes móviles y menos trabajo manual
recurrente.

## Stack (cerrado)

- **Django 5.2 LTS** sobre Python 3.13. El admin resuelve la aprobación, la suspensión, la moderación y la carga de datos.
- **Páginas renderizadas en el servidor** con CSS propio. No hay build de frontend.
- **SQLite en modo WAL**, en un volumen local compartido por `web` y `worker`.
- **Un worker sin Celery ni Redis:** el comando `manage.py worker` corre en loop, con la misma imagen que la web.
- **Dependencias de runtime:** Django, gunicorn, whitenoise y Pillow, más sus transitivas, `asgiref` y `sqlparse`. Las versiones exactas están fijadas en `requirements.txt`.

## Estructura del repo

```
serviciosrgl/
├── config/            settings (todo por env), urls, wsgi
├── apps/
│   ├── core/          City, Barrio, páginas legales/estáticas, home
│   ├── directorio/    Rubro, Oficio, FotoTrabajo, franjas de guardia
│   ├── pedidos/       Pedido (presupuesto/urgencia), respuestas, ruteo,
│   │                  notificadores, comando `worker`
│   └── resenas/       Resena + moderación; Reclamo/Reporte/Recomendación
├── templates/         base.html y parciales compartidos
├── static/css/        CSS propio, mobile-first
├── data/              (no versionado) db.sqlite3 + media/  ← volumen
├── Dockerfile         una imagen
├── compose.yml        servicios web + worker
├── requirements.txt   versiones exactas
└── .env.example
```

## SQLite: detalles que importan

- **`transaction_mode = IMMEDIATE`.** Con el modo por defecto (DEFERRED), una transacción que lee y después escribe falla al instante con "database is locked" si otro proceso escribe, aunque haya `busy_timeout`. Lo medimos con 3 procesos haciendo leer→escribir 300 veces cada uno: DEFERRED dio 229 errores sobre 900 y IMMEDIATE, 0.
- **`timeout = 5`**, que equivale a `busy_timeout` de 5 s.
- **PRAGMAs** por conexión: `journal_mode=WAL`, `synchronous=NORMAL` y `foreign_keys=ON`.
- **Se monta el directorio `./data`, no el archivo.** WAL crea `-wal` y `-shm` al lado de la base. Tiene que ser un disco local: NFS o SMB rompen el locking.
- **Backup en caliente** con `sqlite3 data/db.sqlite3 ".backup backup.sqlite3"`. Copiar el archivo con `cp` mientras corre puede dejar fuera lo que todavía está en el `-wal`.

## Despliegue

- **Puerto `127.0.0.1:8000` y reverse proxy delante.** El proxy hace TLS, redirige http→https y sirve `/media/` desde `./data/media`, porque whitenoise solo sirve estáticos. Sin proxy, `SERVE_MEDIA=1` hace que Django sirva media, lo cual sirve para salir del paso.
- **Solo `web` corre `migrate`, al arrancar.** El `worker` espera a que `web` esté sano (`depends_on: service_healthy`), así nunca corre contra un esquema viejo.
- **Los contenedores corren con uid 1000,** así que `./data` tiene que pertenecer a ese uid en el host.
- **`SITE_URL` es obligatorio en producción.** El worker arma los links firmados de los mails y no tiene un request del que sacar el dominio.
- **Rotación de `SECRET_KEY`:** la clave vieja va a `DJANGO_SECRET_KEY_FALLBACKS`. Si no, se invalidan los links firmados que estén en vuelo.

## Multi-ciudad (preparado, no construido)

- `City` es un modelo. `Barrio`, `Oficio` y `Pedido` tienen FK a `City`. `Rubro` es global.
- Todas las URLs públicas cuelgan de `/<ciudad>/`, y `/` redirige a `/rio-gallegos/`.
- Cada consulta filtra por la ciudad que viene en la URL. No hay selector ni onboarding.

## Pedidos y urgencias

- **Responder o aceptar un pedido es siempre un link firmado** (`django.core.signing`), sin importar el canal por el que llegue.
- **El notificador es una interfaz con backends** que se eligen por setting (`NOTIFICADORES`):
  - v1: `email` y `semimanual`, que le avisa al admin con un link `wa.me` listo para reenviar.
  - Siguiente: `telegram`.
  - Más adelante: `whatsapp`.
- **Presupuesto:** fan-out a los oficios del rubro que cubren el barrio. Cada uno responde por link y el vecino elige.
- **Urgencia:** se arma una cola de oficios de guardia ordenada por `ultima_asignacion` ascendente, salteando a los no disponibles. Si el primero no acepta en `URGENCIA_MINUTOS_PARA_ACEPTAR`, el worker escala al siguiente. Cuando se termina la cola, entra el fallback: lista de oficios con consentimiento y teléfono visible, más un aviso al admin.
- **La disponibilidad ("no disponible hasta…") también se maneja por link firmado.** Los oficios no tienen login.

## Modelo de datos

```
core        City ─┬─< Barrio
                  │
directorio        ├─< Oficio >──< Rubro          (N:N rubros)
                  │     ├──< Barrio               (N:N barrios, o cubre_toda_la_ciudad)
                  │     ├─< FranjaGuardia         (día + desde/hasta, semanal)
                  │     ├─< FotoTrabajo
                  │     └─< Consentimiento        (append-only, Ley 25.326)
pedidos           ├─< Pedido ─< Derivacion >─ Oficio   (una por pedido×oficio)
                  │     └─< FotoPedido
resenas           │   Pedido ─ Resena >─ Oficio  (1:1 con pedido, o bootstrap)
                  │   Pedido ─< Reclamo
                  └─< Reporte                     (recomendación / advertencia)
```

- **Verificado** = `estado=aprobado` + `dni_verificado_at`. No se guarda el número de DNI (minimización de datos). La matrícula se exige al aprobar si algún rubro tiene `requiere_matricula`. Esa regla se valida en el admin, porque es M2M y el modelo no la puede chequear al guardar.
- **Franjas de guardia:**
  - Si `hasta < desde`, la franja cruza la medianoche.
  - Si `desde == hasta`, cubre el día completo.
  - Se evalúan en `City.timezone`.
- **`Derivacion`** es el `IntentoNotificacion` del spec más la respuesta del oficio (mensaje y monto en presupuesto). El link firmado lleva `derivacion_id` + `Oficio.version_links`. No se guarda token: subir la versión invalida todos los links de ese oficio.
- **La cola de urgencia no se guarda.** En cada escalado se elige al próximo elegible que todavía no tenga derivación en ese pedido. Así, si alguien se marca no disponible a mitad de camino, la cola lo respeta.
- **Pedido:**
  - **Contacto y aviso al vecino.** El mail es obligatorio en presupuesto y opcional en urgencia. `token` es un token aleatorio guardado, no firmado: tiene que durar meses (por la reseña) y sobrevivir a una rotación de `SECRET_KEY`.
  - **Completado.** Lo confirma el vecino. Si no lo hace, el worker lo completa a las `PEDIDO_HORAS_AUTOCOMPLETAR` (24 h) de asignado y registra quién lo completó en `completado_por`.
- **Reseña:**
  - Una por pedido, solo si el pedido está completado y es del oficio asignado.
  - Sin pedido, únicamente si es de bootstrap (lo asegura una constraint en la base).
  - Se publica al instante y el admin puede ocultarla (moderación posterior).

## Monetización (no se construye, solo no se bloquea)

- `base.html` tiene un bloque de footer reservado para sponsor o Cafecito, que se prende con un setting.
- Un futuro "destacado" se resuelve con un campo `destacado_hasta` y un `order_by`.
