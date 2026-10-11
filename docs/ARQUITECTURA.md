# Arquitectura y decisiones — Servicios RGL

Documento vivo. Cada decisión tiene su "por qué" para que dentro de un año
sepamos si sigue valiendo.

## Principio rector

El costo del proyecto es el tiempo del admin, no el hosting. Ante dos
opciones, gana la que tiene menos partes móviles y menos trabajo manual
recurrente.

---

## 1. Stack

| Pieza | Elección | Por qué |
|---|---|---|
| Framework | **Django 5.2 LTS** (Python 3.12) | El **admin viene incluido**: aprobar, suspender, moderar y cargar datos sale casi gratis, sin escribir un panel. ORM con migraciones, auth, formularios con validación y CSRF incluidos. LTS con parches de seguridad hasta abril 2028 (después saltamos a la 6.2 LTS). |
| Render | Templates de Django (SSR) + CSS propio y chico | Sin build de frontend, sin node_modules. Mobile-first con CSS plano. JS solo donde haga falta, inline y chico. |
| Base de datos | **SQLite (modo WAL)** para arrancar | 15-20 oficios y tráfico bajo: SQLite sobra. El backup es copiar un archivo. Si algún día hace falta, pasar a PostgreSQL es cambiar `DATABASES` + `dumpdata/loaddata`; el código no cambia. |
| Tareas en segundo plano | **Un comando de Django** (`manage.py procesar_urgencias`) que corre en loop en un contenedor `worker` (misma imagen) | El escalado de urgencias es la única tarea "con reloj". No hace falta Celery + Redis: el estado vive en la base (timestamps), así que el comando es idempotente y sobrevive reinicios. Cada ~30 s mira qué pedidos vencieron y escala. |
| Servidor | gunicorn + whitenoise (estáticos) | Un solo proceso, sin nginx obligatorio para los estáticos. Adelante va el reverse proxy que vos ya uses (Caddy/nginx) para TLS. |
| Imágenes | Pillow, redimensionado al subir | Las fotos de trabajos se achican a un tamaño razonable al guardarlas. Sin CDN ni servicios externos. |
| Deploy | Docker + `compose.yml` (servicios `web` y `worker`, volúmenes para `db.sqlite3` y `media/`) | Reproducible, un `docker compose up -d` y listo. |
| Dependencias | `pyproject.toml` + `uv.lock` | Lock reproducible. Runtime: Django, gunicorn, whitenoise, Pillow. Nada más por ahora. |
| Zona horaria | `America/Argentina/Rio_Gallegos` | Las guardias dependen de "qué hora es en la ciudad". Se guarda en UTC y se interpreta en hora local. Cuando se sumen ciudades, cada `City` tiene su `timezone`. |

### Descartado a propósito

- **Celery/Redis/RQ**: un servicio más que mantener para una sola tarea periódica.
- **Next.js / SPA**: build de frontend, más superficie de dependencias que actualizar.
- **WhatsApp Business API en v1**: ver §4, queda detrás de una interfaz para sumarlo sin reescribir.

---

## 2. Estructura del repo

```
serviciosrgl/
├── README.md                 # cómo levantar en local y en el VPS
├── docs/
│   └── ARQUITECTURA.md       # este archivo
├── pyproject.toml / uv.lock
├── manage.py
├── config/                   # settings, urls raíz, wsgi
│   └── settings.py           # todo configurable por variables de entorno
├── apps/
│   ├── core/                 # City, Barrio, páginas legales, layout base, contexto de ciudad
│   ├── directorio/           # Trade (rubro), Professional, fotos, sello Verificado, guardias
│   ├── pedidos/              # Request, ruteo y escalado de urgencias, notificadores
│   └── resenas/              # Review atada a un pedido
├── templates/                # base.html + parciales compartidos
├── static/                   # css/, img/
├── compose.yml
├── Dockerfile
└── .env.example
```

Cuatro apps chicas con límites claros. `pedidos` no sabe cómo se manda un
mensaje: le pide a un *notificador* (§4).

---

## 3. Multi-ciudad (preparado, no construido)

- `City` es un modelo; `Barrio`, `Professional` y `Request` tienen FK a `City`.
- `Trade` (rubro) es **global**: plomería es plomería en cualquier ciudad.
- Todas las URLs públicas cuelgan de `/<ciudad>/`: `/rio-gallegos/plomeria/`,
  `/rio-gallegos/urgencia/`. La raíz `/` redirige a `/rio-gallegos/`.
- Las consultas filtran siempre por la ciudad de la URL.
- **No** hay selector de ciudad ni onboarding: hay una sola fila en `City`.

Sumar Caleta Olivia mañana = cargar la ciudad y sus barrios en el admin.

---

## 4. Urgencias: diseño para mejora progresiva

La idea central: **la aceptación de un pedido es siempre un link web firmado**
(`/p/<token>/`), independiente del canal por el que le llegue al oficio.
El canal es intercambiable.

```
Pedido creado ─► ruteo elige oficio #1 ─► Notificador.enviar(oficio, link)
                         │                         (mail hoy; WhatsApp/Telegram mañana)
                         ▼
          worker: ¿pasaron N min sin aceptar? ─► siguiente de guardia
                         │
          ¿se acabó la lista? ─► fallback: el cliente ve la lista de
                                 oficios de guardia con teléfono directo
                                 + aviso al admin
```

- **Notificador**: interfaz con backends `consola` (desarrollo), `email`
  (v1) y luego `whatsapp_cloud` o `telegram`. Se elige por setting.
- **Rotación justa sin calendario**: en lugar de que vos cargues la guardia
  de cada finde, cada oficio declara sus franjas habituales de guardia
  (ej. "sáb 8-22, dom 8-22") y el orden se decide por *quién recibió un
  pedido hace más tiempo* (round-robin automático). Un switch
  "no disponible hasta…" para vacaciones. Cero mantenimiento semanal.
- **Cliente**: al crear el pedido recibe un link de seguimiento
  (`/s/<token>/`) donde ve el estado, el contacto del oficio cuando acepta,
  y más tarde el formulario de reseña. Ese mismo token es lo que habilita
  la reseña: sin pedido, no hay reseña.
- **Texto libre → rubro**: en v1, mapa de palabras clave ("pérdida",
  "caño" → plomería; "olor a gas" → gas). Si no matchea, el usuario elige.

### Privacidad (Ley 25.326)

- Público: barrio, rubro, descripción. Nunca dirección ni teléfono del cliente.
- El oficio ve el teléfono del cliente **solo después de aceptar**.
- Los oficios que se anotan para guardia aceptan que su teléfono aparezca en
  la lista de fallback (consentimiento explícito en el alta).
- Datos de pedidos viejos se pueden anonimizar con un comando (a definir el plazo).

---

## 5. Admin

Django admin con acciones propias:
- **Aprobar** (pendiente → aprobado, marca fecha y "Verificado").
- **Suspender** exige motivo; el perfil sale de búsquedas y del ruteo, las
  reseñas quedan.
- Validación: si algún rubro del oficio `requiere_matricula`, la matrícula es
  obligatoria para aprobar.

---

## 6. Monetización (no se construye, solo no se bloquea)

- `base.html` tiene un bloque de footer donde después entra un sponsor o un
  botón de Cafecito, prendible por setting.
- Un futuro "destacado" es un campo `destacado_hasta` y un `order_by`. No
  requiere tocar nada hoy.
