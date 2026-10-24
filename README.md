# Backend ANUC Zetaquirá

Esqueleto del backend (FastAPI, Python 3.12) para desplegar en AWS ECS Fargate. Por ahora tiene el webhook de WhatsApp (recibe, valida y encola en SQS FIFO) y la capa de datos (SQLAlchemy 2 + Alembic sobre PostgreSQL 16 con PostGIS y pgvector). Sigue la estructura y las convenciones de `BACKEND_GUIA.md`.

## Endpoints

| Método y ruta | Qué hace |
|---|---|
| `GET /salud` | `{"status": "ok"}`. No depende de nada externo (es el health check del ALB). |
| `GET /webhook/whatsapp` | Verificación de Meta: si `hub.mode=subscribe` y `hub.verify_token` coincide, devuelve `hub.challenge` en texto plano; si no, `403`. |
| `POST /webhook/whatsapp` | Valida `X-Hub-Signature-256` (si falla, `401`), encola cada mensaje en SQS y responde `200`. Si SQS falla, responde `500` para que Meta reintente. |

Cada mensaje se publica en `mensajes-entrantes.fifo` con:
- `MessageGroupId` = celular del remitente, para conservar el orden de cada conversación.
- `MessageDeduplicationId` = `wamid`, para que un reintento de Meta no genere un duplicado.

Cuerpo publicado:

```json
{"wamid": "wamid.X", "from": "573001234567", "type": "image", "timestamp": "1760000000",
 "content": {"media_id": "MEDIA123", "mime_type": "image/jpeg", "caption": "Hojas con manchas"}}
```

Los eventos `statuses` (confirmaciones de entrega) se ignoran.

## Estructura

```
app/
├─ main.py                       # crea la app, middleware, routers
├─ api/
│  ├─ salud_router.py            # GET /salud
│  ├─ v1_webhook_router.py       # GET/POST /webhook/whatsapp
│  └─ errors.py                  # formato de error {codigo, mensaje, campos}
├─ core/
│  ├─ config.py                  # Settings y DatabaseSettings (pydantic-settings)
│  ├─ logging.py                 # logs JSON con correlation_id, X-Request-Id
│  └─ passwords.py               # hash/verificación Argon2
├─ modules/webhook/
│  ├─ signature.py               # HMAC-SHA256 en tiempo constante
│  ├─ payload_adapter.py         # WhatsAppPayloadAdapter
│  └─ service.py                 # encolado
└─ infrastructure/
   ├─ sqs_queue_client.py        # cliente boto3 (cadena de credenciales por defecto)
   └─ db/
      ├─ base.py                 # Base declarativa + convención de nombres
      ├─ ids.py                  # PREFIJOS_ID y generador de ids con secuencias
      ├─ session.py              # engine y sesión async
      ├─ seed.py                 # semillas idempotentes
      └─ models/                 # modelos por módulo (catalogos, territorio, casos, ...)
migrations/                      # Alembic (la URL sale de DATABASE_URL)
tests/                           # espejo de app/
localstack/init-sqs.sh           # crea la cola FIFO al levantar LocalStack
docker/postgres/                 # imagen con PostGIS + pgvector
scripts/firmar.py                # genera la firma de prueba
scripts/ejemplos/*.json          # payloads de ejemplo
```

## Configuración

Las variables se leen del entorno (en local también de `.env`; copia `.env.example`):

| Variable | Obligatoria | Descripción |
|---|---|---|
| `AWS_REGION` | no (`us-east-1`) | Región de SQS |
| `AWS_ENDPOINT_URL` | no | Solo para LocalStack (`http://localhost:4566`) |
| `SQS_QUEUE_URL` | sí | URL de la cola FIFO |
| `WHATSAPP_VERIFY_TOKEN` | sí | Token que configuras en Meta para la verificación |
| `WHATSAPP_APP_SECRET` | sí | App Secret de la app de Meta (firma HMAC) |
| `LOG_LEVEL` | no (`INFO`) | Nivel de log |
| `DATABASE_URL` | sí | `postgresql+psycopg://usuario:clave@host:5432/anuc_db` (en RDS, con `?sslmode=require`) |
| `EMBEDDING_DIM` | no (`1024`) | Dimensión de los embeddings. Cambiarla obliga a recalcular todos los vectores. |

Si falta una variable obligatoria, la app no arranca. Las credenciales de AWS **nunca** van en el código: en ECS se usa el rol IAM de la tarea; en local, un perfil o variables de entorno.

## Ejecutar en local

### Opción A: todo con Docker (recomendada)

Requiere Docker Desktop.

```bash
docker compose up --build
```

Esto levanta:
- `api` en http://localhost:8000
- `localstack` (solo SQS) en http://localhost:4566, que crea `mensajes-entrantes.fifo` al arrancar
- `db`: PostgreSQL 16 con PostGIS y pgvector en `localhost:5432` (base `anuc_db`, usuario `anuc`, contraseña `anuc_local`).

La API no arranca hasta que la cola existe. Los valores locales son `WHATSAPP_VERIFY_TOKEN=token-local` y `WHATSAPP_APP_SECRET=secreto-local`.

### Opción B: API en tu máquina y LocalStack en Docker

```bash
docker compose up -d localstack
python -m venv .venv
.venv\Scripts\activate          # Windows (en Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env          # Linux/macOS: cp .env.example .env
uvicorn app.main:app --reload
```

## Base de datos

### Migraciones y semillas

```bash
docker compose up -d db
alembic upgrade head
python -m app.infrastructure.db.seed
```

Con todo en Docker, se corren dentro del contenedor de la API (la imagen incluye `alembic.ini` y `migrations/`):

```bash
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m app.infrastructure.db.seed
```

**Contra RDS**, la misma imagen sirve como tarea puntual de ECS: se sobreescribe el comando con `alembic upgrade head` y se pasa `DATABASE_URL` desde Secrets Manager. El usuario debe poder crear las extensiones (`rds_superuser`). Para revisar el SQL sin conectarse: `alembic upgrade head --sql`.

### Identificadores

Los ids los genera la aplicación con una secuencia por tabla y la función SQL `fn_generar_id`. Los prefijos están en un solo diccionario, `PREFIJOS_ID` (`app/infrastructure/db/ids.py`):

| Largo | Formato | Ejemplos |
|---|---|---|
| 5 (catálogos) | 2 letras + 3 dígitos | `CF001` categorías, `ES001` estados, `EP001` especies |
| 10 | 3 letras + `-` + 6 dígitos | `PRO-000001`, `FIN-`, `CUL-`, `TEC-`, `LID-`, `PRE-`, `PAR-`, `ALR-` |
| 12 | 3 letras + `-` + 8 dígitos | `CAS-00000001`, `IDP-`, `EVE-`, `DIA-`, `RES-`, `FOT-`, `AUD-`, `MSG-`, `CON-` |

Al hacer `flush`, la sesión asigna el id a los objetos nuevos que no lo traen.

### Ubicación geográfica

`fincas.ubicacion` y `cultivos.ubicacion` son `geometry(Point, 4326)` **generadas** (`GENERATED ALWAYS AS ... STORED`) a partir de longitud y latitud, con índice GiST. No se escriben: se actualizan solas cuando cambian las coordenadas.

## Tests

```bash
pip install -r requirements-dev.txt
pytest --cov=app --cov-report=term-missing
```

Los del webhook no necesitan Docker ni AWS: SQS se simula con `moto`. Los de base de datos (`tests/infrastructure/db/`) usan el Postgres del compose (`docker compose up -d db`) y una base aparte, `anuc_test`, que crean solos. Si Postgres no está levantado, esos tests se omiten. Para usar otro servidor: `TEST_DATABASE_URL`. Cubren la verificación del webhook, la firma válida e inválida, que un mensaje de imagen llegue a SQS con `MessageGroupId` y `MessageDeduplicationId` correctos, que un evento `statuses` no genere envío, la deduplicación de reintentos y el `500` cuando SQS falla.

## Probar el webhook con curl

Los ejemplos usan los valores de `docker compose`. En Windows, ejecútalos en **Git Bash** (en PowerShell, `curl` es un alias de otro comando).

**1. Salud**

```bash
curl -i http://localhost:8000/salud
```

**2. Verificación de Meta** (debe responder `200` y `12345`)

```bash
curl -i "http://localhost:8000/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=token-local&hub.challenge=12345"
```

Con un token incorrecto responde `403`.

**3. Generar la firma**

La firma es `sha256=` + HMAC-SHA256 en hexadecimal de los **bytes exactos** del cuerpo, usando el App Secret como clave. Si cambias un solo espacio del JSON, la firma deja de valer, por eso se firma el archivo y se envía con `--data-binary`.

Con el script del proyecto:

```bash
python scripts/firmar.py scripts/ejemplos/imagen.json secreto-local
```

O con openssl:

```bash
echo "sha256=$(openssl dgst -sha256 -hmac 'secreto-local' -r scripts/ejemplos/imagen.json | cut -d' ' -f1)"
```

**4. Enviar un mensaje de imagen firmado** (debe responder `200`)

```bash
FIRMA=$(python scripts/firmar.py scripts/ejemplos/imagen.json secreto-local)
curl -i -X POST http://localhost:8000/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: $FIRMA" \
  --data-binary @scripts/ejemplos/imagen.json
```

**5. Ver el mensaje en la cola de LocalStack**

```bash
docker compose exec localstack awslocal sqs receive-message \
  --queue-url http://localhost:4566/000000000000/mensajes-entrantes.fifo \
  --attribute-names All --message-system-attribute-names All
```

Deberías ver `MessageGroupId: 573001234567` y `MessageDeduplicationId: wamid.PRUEBA001`. Si reenvías el mismo archivo en los siguientes 5 minutos, SQS lo descarta por la deduplicación.

**6. Casos negativos**

```bash
# Firma inválida → 401
curl -i -X POST http://localhost:8000/webhook/whatsapp \
  -H "X-Hub-Signature-256: sha256=0000" --data-binary @scripts/ejemplos/imagen.json

# Evento de estado con firma válida → 200, pero no encola nada
FIRMA=$(python scripts/firmar.py scripts/ejemplos/estado.json secreto-local)
curl -i -X POST http://localhost:8000/webhook/whatsapp \
  -H "X-Hub-Signature-256: $FIRMA" --data-binary @scripts/ejemplos/estado.json
```

Los logs salen en JSON por stdout (`docker compose logs -f api`), con `correlation_id` y el celular enmascarado (`***4567`).

## Despliegue en AWS (notas)

- Imagen: `docker build -t anuc-backend:1.0 .` (python:3.12-slim, usuario no root, uvicorn en `0.0.0.0:8000`).
- ALB: health check en `/salud`, puerto 8000.
- El rol de la tarea ECS necesita `sqs:SendMessage` sobre la cola.
- `WHATSAPP_VERIFY_TOKEN` y `WHATSAPP_APP_SECRET` se inyectan desde Secrets Manager. No definas `AWS_ENDPOINT_URL`.
- En Meta, la URL del webhook es `https://<dominio>/webhook/whatsapp`.
