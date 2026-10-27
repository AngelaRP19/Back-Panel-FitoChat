# Backend ANUC Zetaquirá

API del panel fitosanitario y webhook del asistente de WhatsApp (FastAPI, Python 3.12) para AWS ECS Fargate. Se desarrolla módulo por módulo según `BACKEND_GUIA.md`.

Estado: módulo 0b (operación y despliegue) listo para revisión. Ya hay configuración, `/api/v1`, CORS, validación de JWT de Cognito, `GET /api/v1/me`, CI, el webhook que encola en SQS y la capa de datos (SQLAlchemy 2 + Alembic sobre PostgreSQL 16 con PostGIS y pgvector).

## Rutas

| Método y ruta | Auth | Qué hace |
|---|---|---|
| `GET /salud` | pública | `{"status": "ok"}`. Sin dependencias externas (health check del ALB). |
| `GET /webhook/whatsapp` | token de verificación | Registro del webhook en Meta: devuelve `hub.challenge` si el token coincide; si no, `403`. |
| `POST /webhook/whatsapp` | firma HMAC | Valida `X-Hub-Signature-256` (`401` si falla), encola en SQS y responde `200`. Si SQS falla, `500` para que Meta reintente. |
| `GET /api/v1/me` | Bearer (access token de Cognito) | Cuenta de la sesión `{id, nombre, correo, celular, rol, activo}`. Es la fuente del rol. |
| `GET /docs`, `/openapi.json` | — | Solo fuera de `prod`. |

Errores con un solo formato, sin envoltorio: `{"codigo": "...", "mensaje": "...", "campos": {...}}`. Todas las respuestas llevan `X-Request-Id`, cabeceras de seguridad y `Cache-Control: no-store`. El cuerpo de una solicitud no puede superar `MAX_BODY_BYTES` (10 MB; `413` si lo supera).

Códigos de `/api/v1/me`: `401 NO_AUTENTICADO` (sin token o inválido), `401 TOKEN_VENCIDO`, `403 CUENTA_INACTIVA`, `403 SIN_PERMISO` (el `sub` no tiene cuenta), `503 ERROR_INTERNO` (no se pudo consultar el JWKS de Cognito).

## Estructura

```
app/
├─ main.py                       # create_app(): middlewares, CORS, routers
├─ api/                          # salud, webhook, v1_panel_router (agrega /api/v1/*), errors
├─ core/
│  ├─ config.py                  # Settings y DatabaseSettings
│  ├─ security_cognito.py        # JWT, usuario_actual, requiere_rol
│  ├─ tokens_prueba.py           # claves y tokens de prueba (solo local y test)
│  ├─ middleware.py              # cabeceras de seguridad y límite de cuerpo
│  └─ logging.py · passwords.py · roles.py · telefonos.py
├─ modules/
│  ├─ webhook/                   # firma, adaptador, encolado
│  └─ panel_api/cuentas/         # GET /me (los demás dominios se suman por módulo)
└─ infrastructure/
   ├─ sqs_queue_client.py
   └─ db/                        # base, models/, ids.py, session.py, seed.py, cuentas_repository.py
migrations/                      # Alembic (la URL sale del entorno)
tests/                           # espejo de app/
docker/postgres/                 # imagen con PostGIS + pgvector
localstack/init-sqs.sh           # crea la cola FIFO en LocalStack
scripts/                         # firmar.py y payloads de ejemplo
.github/workflows/ci.yml
```

## Variables de entorno

Nunca van en el repositorio: en local, `.env` (copia `.env.example`); en AWS, valores normales o referencias a Secrets Manager. Una variable vacía cuenta como no definida y, si falta una obligatoria, la app no arranca.

| Variable | Obligatoria | Descripción |
|---|---|---|
| `APP_ENV` | no (`local`) | `local`, `test` o `prod` |
| `INTEGRACION_WHATSAPP`, `INTEGRACION_IA` | no (`simulada`) | `simulada` o `real` |
| `DATABASE_URL` | una de las dos formas | `postgresql+psycopg://usuario:clave@host:5432/anuc_db`; tiene prioridad |
| `DB_HOST`, `DB_USER`, `DB_PASSWORD` | (forma por piezas) | Conexión a RDS; la contraseña puede traer símbolos |
| `DB_PORT`, `DB_NAME`, `DB_SSLMODE` | no (`5432`, `anuc_db`, `require`) | Con el Postgres del compose: `DB_SSLMODE=disable` |
| `EMBEDDING_DIM` | no (`1024`) | Dimensión de los embeddings. Cambiarla obliga a recalcular los vectores |
| `AWS_REGION` | no (`us-east-1`) | Región |
| `AWS_ENDPOINT_URL` | no | Solo LocalStack. Prohibida en `prod` |
| `SQS_QUEUE_URL` | sí | Cola FIFO |
| `S3_BUCKET_EVIDENCIAS` | en `prod` | Bucket de evidencias |
| `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID` | en `prod` | Validación del JWT |
| `COGNITO_REGION` | no | Por defecto, `AWS_REGION` |
| `CORS_ORIGINS` | no (vacío) | Orígenes separados por comas; `*` no se acepta |
| `MAX_BODY_BYTES` | no (10 MB) | Tamaño máximo del cuerpo |
| `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` | sí | Webhook |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | con `INTEGRACION_WHATSAPP=real` | Envío |
| `LOG_LEVEL` | no (`INFO`) | Nivel de log |

Reglas de arranque: en `prod` son obligatorios Cognito y el bucket; en ECS (`AWS_EXECUTION_ENV`) fuera de `prod` la app no arranca, porque `local` y `test` aceptan tokens firmados con una clave de pruebas.

## Ejecutar en local

Requiere Docker Desktop.

```bash
docker compose up --build
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m app.infrastructure.db.seed
```

Levanta la API en http://localhost:8000, LocalStack (SQS) en :4566 y PostgreSQL 16 con PostGIS y pgvector en :5432 (base `anuc_db`, usuario `anuc`, contraseña `anuc_local`).

Sin Docker para la API (solo base y colas en Docker):

```bash
docker compose up -d db localstack
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env          # Linux/macOS: cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

En Windows, `--reload` hace que `uvicorn` use el event loop compatible con el driver asíncrono de `psycopg`; sin él, `/api/v1/me` falla. En Docker (Linux) no hace falta.

### Probar `GET /api/v1/me` en local

En `local`, sin User Pool configurado, la API valida tokens firmados con una clave de pruebas que guarda fuera del repositorio. Hace falta una cuenta con `cognito_sub`:

```bash
docker compose exec db psql -U anuc -d anuc_db -c "INSERT INTO lideres_tecnicos (id_lider_tecnico, nombre_lider_tecnico, correo_lider_tecnico, celular_lider_tecnico, contrasena_lider_tecnico, estado_cuenta_lider_tecnico, cognito_sub) VALUES ('LID-000001', 'Líder de prueba', 'lider@demo.test', '573000000001', 'sin-uso', 'ACTIVO', 'sub-de-prueba')"
TOKEN=$(docker compose exec -T api python -m app.core.tokens_prueba sub-de-prueba)
curl -i http://localhost:8000/api/v1/me                                      # 401 NO_AUTENTICADO
curl -i -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/me    # 200, rol LIDER_TECNICO
```

Si defines `COGNITO_USER_POOL_ID` y `COGNITO_APP_CLIENT_ID`, `local` valida contra el User Pool real y el comando de tokens se niega a emitir. El frontend debe enviar el **access token**, no el ID token.

## Tests, lint y tipos

```bash
pip install -r requirements-dev.txt
docker compose up -d db                       # Postgres para los tests de base de datos
pytest --cov=app --cov-report=term-missing
ruff check . && ruff format --check .
mypy app
```

Los tests de base de datos y de `/me` usan una base aparte, `anuc_test`, que crean solos (otra: `TEST_DATABASE_URL`). Sin Postgres levantado, esos tests se omiten. SQS se simula con `moto`. La CI (`.github/workflows/ci.yml`) corre lo mismo con cobertura mínima de 80 % y construye la imagen.

## Base de datos

- **Migraciones y semillas:** `alembic upgrade head` y `python -m app.infrastructure.db.seed`. Con `alembic upgrade head --sql` se revisa el SQL sin conectarse.
- **Ids:** los genera la aplicación con una secuencia por tabla y `fn_generar_id`. Los prefijos están en `PREFIJOS_ID` (`app/infrastructure/db/ids.py`): `CF001` (catálogos de 5), `PRO-000001` (10), `CAS-00000001` (12). La sesión los asigna al hacer `flush`.
- **Ubicación:** `fincas.ubicacion` y `cultivos.ubicacion` son `geometry(Point, 4326)` generadas a partir de longitud y latitud, con índice GiST. No se escriben.
- **Contraseñas:** las columnas `contrasena_*` guardan solo hashes Argon2 (`app/core/passwords.py`); las credenciales reales viven en Cognito.

## Probar el webhook con curl

Los ejemplos usan los valores de `docker compose` (`token-local`, `secreto-local`). En Windows, ejecútalos en Git Bash.

```bash
curl -i http://localhost:8000/salud
curl -i "http://localhost:8000/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=token-local&hub.challenge=12345"   # 200 y 12345
```

La firma es `sha256=` + HMAC-SHA256 en hexadecimal de los **bytes exactos** del cuerpo con el App Secret como clave; por eso se firma el archivo y se envía con `--data-binary`:

```bash
FIRMA=$(python scripts/firmar.py scripts/ejemplos/imagen.json secreto-local)
curl -i -X POST http://localhost:8000/webhook/whatsapp \
  -H "Content-Type: application/json" -H "X-Hub-Signature-256: $FIRMA" \
  --data-binary @scripts/ejemplos/imagen.json
docker compose exec localstack awslocal sqs receive-message \
  --queue-url http://localhost:4566/000000000000/mensajes-entrantes.fifo \
  --attribute-names All --message-system-attribute-names All
```

Cada mensaje se publica en `mensajes-entrantes.fifo` con `MessageGroupId` = celular del remitente y `MessageDeduplicationId` = `wamid`. Los eventos `statuses` se ignoran. Una firma inválida responde `401`.

## Despliegue en AWS

La imagen trae `app`, `alembic.ini` y `migrations`, y se versiona (`anuc-backend:1.1`; no se reutiliza `1.0`). Un mismo código, distintos comandos:

| Necesidad | Comando | Cómo |
|---|---|---|
| API | `uvicorn app.main:app` (el `CMD`) | Servicio ECS detrás del ALB |
| Migrar | `alembic upgrade head` | Tarea ECS puntual con las variables `DB_*` |
| Sembrar | `python -m app.infrastructure.db.seed` | Tarea ECS puntual |

Variables en la Task Definition: `APP_ENV=prod`, `AWS_REGION`, `SQS_QUEUE_URL`, `S3_BUCKET_EVIDENCIAS`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_SSLMODE=require`, `COGNITO_*`, `CORS_ORIGINS`, `INTEGRACION_WHATSAPP=simulada`, `INTEGRACION_IA=simulada`. Desde Secrets Manager (`ValueFrom`): `DB_USER` y `DB_PASSWORD` (claves `username` y `password` del secreto de RDS) y las de WhatsApp. No definas `AWS_ENDPOINT_URL`.

Permisos del rol de la tarea que usa hoy el código: `sqs:SendMessage` sobre la cola. Los demás (S3, Cognito) se piden en los módulos que los usan. El health check del ALB va a `/salud`, puerto 8000.
