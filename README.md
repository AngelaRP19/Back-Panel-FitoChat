# Backend ANUC Zetaquirá

API del panel fitosanitario y webhook del asistente de WhatsApp (FastAPI, Python 3.12) para AWS ECS Fargate. Se desarrolla módulo por módulo según `BACKEND_GUIA.md`.

Estado: módulos 0b (operación y despliegue), 1b (ajustes del modelo y semillas v2), 1c (objetos SQL), 1d (datos de demostración), 2 (cuentas y acceso) y 3 (productores, fincas y cultivos) 4 (casos: bandeja y detalle) 5 (diagnóstico y cierre) 6 (alertas del panel) 7 (resumen e inicio) 8 (jobs programados) 9 (consumidor de la cola) y 10 (orquestador conversacional) listos para revisión. Ya hay configuración, `/api/v1`, CORS, validación de JWT de Cognito, `GET /api/v1/me`, CI, el webhook que encola en SQS y la capa de datos (SQLAlchemy 2 + Alembic sobre PostgreSQL 16 con PostGIS y pgvector) con los ajustes aprobados el 26-oct-2026. Sigue el módulo 11: pipeline de imágenes, identificación y clientes reales (Fase C).

## Rutas

| Método y ruta | Auth | Qué hace |
|---|---|---|
| `GET /salud` | pública | `{"status": "ok"}`. Sin dependencias externas (health check del ALB). |
| `GET /webhook/whatsapp` | token de verificación | Registro del webhook en Meta: devuelve `hub.challenge` si el token coincide; si no, `403`. |
| `POST /webhook/whatsapp` | firma HMAC | Valida `X-Hub-Signature-256` (`401` si falla), encola en SQS y responde `200`. Si SQS falla, `500` para que Meta reintente. |
| `GET /api/v1/me` | Bearer (access token de Cognito) | Cuenta de la sesión `{id, nombre, correo, celular, rol, activo}`. Es la fuente del rol. |
| `GET /api/v1/auth/estado` | pública | `{hay_lider}`: el frontend decide si muestra el registro del primer líder. |
| `POST /api/v1/auth/primer-lider` | pública (solo funciona sin líderes) | Crea el primer líder en Cognito y en la base. `201 {id}`; `409 YA_EXISTE_LIDER` si ya hay uno. |
| `POST /api/v1/auth/recuperacion/verificar-correo` | pública, 5 por minuto por IP | `{existe}`; `429` con `Retry-After` al excederse. |
| `PATCH /api/v1/me`, `POST /api/v1/me/contrasena` | Bearer | Editar nombre y celular (el correo no se cambia) y cambiar la contraseña (`204`). |
| `GET/POST /api/v1/tecnicos`, `GET/PATCH /api/v1/tecnicos/{id}`, `PATCH /api/v1/tecnicos/{id}/estado` | Bearer, solo líder técnico | Administración de cuentas de líderes y técnicos. `GET` admite `q`, `rol`, `activo`, `carga`, `page`, `page_size` y `con_carga=true` (lista para asignar casos). `409 ULTIMA_CUENTA_ACTIVA` si se intenta deshabilitar la última cuenta activa de un rol. |
| `GET/POST /api/v1/productores`, `GET/PATCH /api/v1/productores/{id}`, `PATCH /api/v1/productores/{id}/estado` | Bearer, solo líder técnico | Productores. `GET` combina `q`, `estado`, `vereda`, `especie`, `con_casos`, `finca`, `desde`, `hasta`, `consulta` (30/90 días) y paginación. Celular único (`409 DUPLICADO`). Deshabilitar no borra casos. |
| `GET/POST /api/v1/fincas`, `GET/PATCH /api/v1/fincas/{id}`, `GET/POST /api/v1/fincas/{id}/cultivos`, `PATCH /api/v1/cultivos/{id}` | Bearer, solo líder técnico | Fincas y cultivos (`INVERNADERO` o `CAMPO_ABIERTO`, mínimo una especie). Solo se registra finca para un productor activo. Coordenadas raras: `advertencias`, sin bloquear. Quitar una especie que usan casos: `409 ESPECIE_EN_USO` con `detalle`. |
| `GET /api/v1/especies`, `/categorias`, `/veredas` | Bearer (líder o técnico) | Catálogos. Las veredas son las de las fincas registradas, con su código normalizado. |
| `GET /api/v1/consulta/productores-fincas`, `GET /api/v1/consulta/productores-fincas/{id}` | Bearer, solo técnico | Productores, fincas y cultivos de **sus** casos (asignación vigente), filtrados en SQL. Lo ajeno responde `404`. |
| `GET /api/v1/casos`, `GET /api/v1/casos/conteos` | Bearer (líder o técnico) | Bandeja con filtros combinables (`estado`, `q`, `vereda`, `finca_id`, `cultivo_id`, `especie`, `categoria`, `tecnico_id`, `coincidencia_min`, `vencimiento`, `desde`, `hasta`) y paginación; los conteos usan el mismo alcance y filtros. El técnico solo recibe sus casos asignados (`WHERE` en SQL). |
| `GET /api/v1/casos/{id}` y `/evidencias`, `/cuestionario`, `/explicacion`, `/historial` | Bearer (líder o técnico) | Detalle consolidado con `puede_diagnosticar`, `puede_cerrar` y `puede_asignar` calculados para quien consulta; fotos con URL prefirmada de 10 minutos. Lo que no está asignado al técnico responde `404`. |
| `PATCH /api/v1/casos/{id}/asignacion` | Bearer, solo líder técnico | Asigna (Pendiente de revisión → En revisión) o reasigna (conserva el estado). `409 TRANSICION_INVALIDA` si ya está Registrado, Cerrado, Descartado o aún no se envía; `409 CASO_YA_ASIGNADO` si es el mismo técnico. |
| `GET /api/v1/resumen/navegacion` | Bearer (líder o técnico) | `{pendientes, alertas_generadas}` para el Sidebar; el técnico ve solo lo suyo. |
| `GET /api/v1/resumen/inicio` | Bearer (líder o técnico) | Indicadores del Inicio; la forma depende del rol (`rol`): el líder ve pendientes, en revisión, cerrados del mes, próximos a vencer, alertas y carga de los técnicos; el técnico, solo lo suyo. |
| `GET /api/v1/resumen/casos-por-categoria?periodo=30d\|90d\|anio` | Bearer, solo líder técnico | Diagnósticos confirmados por categoría en el periodo. |
| `POST /api/v1/casos/{id}/diagnostico` | Bearer, solo técnico | Completa el diagnóstico (En revisión → Registrado, `sp_registrar_diagnostico`). Solo el técnico de la asignación vigente: el líder recibe `403` y otro técnico `404`. `CONFIRMADO` solo si la identificación fue preliminar; `OTRA_PATOLOGIA` exige categoría; `REQUIERE_VISITA`, el motivo. Una categoría transmisible evalúa las alertas en la misma transacción. |
| `POST /api/v1/casos/{id}/cierre` | Bearer (líder o técnico asignado) | Registrado → Cerrado (`sp_cerrar_caso`); `vence_en` = cierre + `MESES_CONSERVACION` (6). Sin reapertura: un caso cerrado responde `409 TRANSICION_INVALIDA`. |
| `GET /api/v1/alertas`, `GET /api/v1/alertas/{id}`, `GET /api/v1/alertas/parametros` | Bearer (líder o técnico) | Alertas con `conteos` por estado, casos que las originan (el técnico solo abre los suyos) y parámetros por categoría. |
| `POST /api/v1/alertas/{id}/confirmar` y `/descartar`, `PUT .../borrador`, `GET .../destinatarios`, `PUT /api/v1/alertas/parametros/{categoria}` | Bearer, solo líder técnico | GENERADA o EN_REVISION → CONFIRMADA o DESCARTADA (con justificación). Destinatarios: productores activos dentro del radio de la alerta, por vereda. El mensaje (máx. 300) no puede recomendar productos, dosis ni tratamientos. |
| `POST /api/v1/alertas/{id}/comunicar` y `/corregir` | Bearer, solo líder técnico | Envían por WhatsApp (simulado hasta la Fase C: estado `SIMULADO`) en lotes, con el resultado por destinatario. Reanudables y sin duplicados. Corregir: solo desde COMUNICADA, una vez, a quienes recibieron la alerta. |
| `POST/DELETE /api/v1/alertas/{id}/seguir` | Bearer, solo técnico | «Avisarme cuando cambie» (P-06; el aviso aún no existe). |
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
│  ├─ catalogos_api.py           # códigos de la API ⇄ nombres guardados en la base (§6.2)
│  └─ logging.py · passwords.py · roles.py · telefonos.py
├─ modules/
│  ├─ webhook/                   # firma, adaptador, encolado
│  ├─ image_pipeline/            # descarga, validación, WebP y S3 de las fotos
│  ├─ identification/            # RAG + IA + reglas del 50 % y los 15 puntos
│  └─ panel_api/cuentas/         # GET /me (los demás dominios se suman por módulo)
└─ infrastructure/
   ├─ sqs_queue_client.py
   ├─ s3_storage_client.py       # evidencias: URLs prefirmadas, descarga y borrado de todas las versiones
   ├─ ia_credenciales.py · bedrock_client.py · rekognition_client.py   # IA en otra cuenta AWS
   └─ db/                        # base, models/, ids.py, session.py, seed.py, procedimientos.py, cuentas_repository.py
migrations/                      # Alembic: 0001 esquema inicial, 0002 ajustes del modelo, 0003 objetos SQL
docs/OBJETOS_SQL.md              # vistas, funciones, procedimientos y triggers del Avance 5
docs/CAMBIOS_MODELO.md           # cambios aprobados al modelo, para actualizar los Anexos 2 y 3
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

### Cuentas y Cognito

Sin User Pool configurado, las altas, ediciones y deshabilitaciones de cuentas usan un Cognito simulado en memoria (útil en `local` y en pruebas). Con User Pool real se usa boto3; el rol de la tarea de ECS necesita `cognito-idp:AdminCreateUser`, `AdminDeleteUser` (compensación si falla la base de datos), `AdminDisableUser`, `AdminEnableUser`, `AdminGetUser`, `AdminUpdateUserAttributes` y `AdminSetUserPassword`. `ChangePassword` usa el access token de la persona y no necesita permiso IAM. Los técnicos nuevos reciben la contraseña temporal por correo desde Cognito; el backend nunca la conoce. El límite de intentos de `verificar-correo` es por proceso (por tarea de ECS).

## Datos de demostración

Para probar el panel con la API real sin WhatsApp (guía, módulo 1d). Requiere las migraciones aplicadas y el bucket de evidencias (LocalStack lo crea con versionado):

```bash
docker compose run --rm api python -m app.infrastructure.db.seed_demo             # siembra (idempotente)
docker compose run --rm api python -m app.infrastructure.db.seed_demo --limpiar   # borrado físico, con las versiones de S3
```

Siembra 22 casos en los siete estados (identificados, ambiguos y no identificados), 9 productores con finca y cultivo en Juracambita, Centro y San Luis, un líder, tres técnicos (uno es la cuenta de técnico del líder), una alerta `GENERADA` de Trips con 4 casos confirmados y otra `COMUNICADA` para probar la corrección. Las fotos son imágenes sintéticas WebP en `evidencias/<año>/<mes>/<id>.webp`.

Todo es reconocible: nombres con sufijo «(demo)», celulares `3000000xxx` y correos `@demo.anuc.test`. En `local` y `test` las cuentas quedan enlazadas a un `cognito_sub` fijo (`demo-<id>`), así que `python -m app.core.tokens_prueba demo-LID-000001` emite un token válido; en `prod` el comando exige `--permitir-demo` y deja el `cognito_sub` vacío para que lo enlace el usuario. Las 3 preguntas del cuestionario también son de demostración: el texto real sigue pendiente (P-B10).

## Jobs programados

Se ejecutan con `python -m app.jobs.run <nombre>` (en AWS, EventBridge Scheduler los lanza como tareas ECS con la misma imagen). Cada uno es idempotente, corre con candado (dos ejecuciones del mismo job no se pisan) y trata cada caso en su propia transacción.

| Job | Frecuencia sugerida | Qué hace |
|---|---|---|
| `advertencia_inactividad` | cada 15 min | Casos del chatbot (Caso creado / En análisis) con 48 h sin actividad: avisa por WhatsApp (simulado hasta la Fase C) y los marca como avisados. |
| `descarte_inactividad` | cada 15 min | Casos del chatbot con 72 h sin actividad: descarte con borrado físico de datos y fotos (todas las versiones en S3) y auditoría sin datos personales. |
| `retencion` | diario | Casos Cerrados cuyo `vence_en` ya llegó (hora de Colombia): borrado físico total. |
| `evaluar_alertas` | diario | Barrido de respaldo de `sp_evaluar_alerta` para las categorías transmisibles. |

Código de salida: `0` bien (o se omitió porque otra ejecución seguía corriendo), `1` hubo errores (revisar los logs), `2` comando o configuración incorrectos (falta `S3_BUCKET_EVIDENCIAS`, WhatsApp no disponible…). Si borrar en S3 falla, no se borra la fila: el siguiente ciclo lo reintenta. Los textos de los avisos son provisionales hasta que el equipo los valide.

## Consumidor de la cola

`python -m app.workers.sqs_consumer` toma los mensajes que el webhook dejó en SQS FIFO (un servicio ECS propio, con la misma imagen; en local: `docker compose --profile consumer up -d consumer`).

- **Una sola vez por mensaje**: el `wamid` es único en `mensajes_whatsapp`. Registrar el mensaje y ejecutar el manejador van en una misma transacción: si algo falla se deshace todo y el reintento lo procesa completo; si llega repetido, se descarta.
- **Borrado tras procesar**, *long polling*, orden por conversación (el celular es el grupo FIFO) y reintentos con *backoff* (5, 10, 20, 40, 60 s). Tras 5 recepciones SQS lo manda a la DLQ `mensajes-entrantes-dlq.fifo`.
- **Apagado ordenado** con SIGTERM/SIGINT: termina el mensaje en curso y libera el resto del lote.
- Al arrancar exige que el *visibility timeout* de la cola (120 s) supere el procesamiento máximo (`SQS_PROCESAMIENTO_MAXIMO_SEGUNDOS`, 90 s); si no, sale con código 2.
- El manejador es el orquestador conversacional (ver abajo). Las respuestas por WhatsApp y el estado de la conversación se aplican **después** de confirmar la transacción: si algo falla, no sale nada y el reintento lo repite completo.

## Chatbot (orquestador conversacional)

Cada mensaje que el consumidor recibe pasa por la máquina de estados de `app/modules/orchestrator/`. El estado de cada conversación (paso, caso en curso, contexto) vive en DynamoDB (`DYNAMODB_TABLA_CONVERSACIONES`; en memoria si no se define, solo fuera de prod); la verdad sobre el caso vive en PostgreSQL.

Flujo: saludo y consentimiento → finca → cultivo → especie y zona afectada → cuestionario → fotos → resumen y envío.

- **La IA conduce las preguntas de síntomas**: la aplicación no las trae escritas. En cada turno le da a la IA el contexto del caso y la última respuesta, y la IA devuelve la siguiente pregunta, si lo respondido sirve y si está fuera del dominio (`app/infrastructure/ai_client.py`). La app solo conecta y envía los mensajes, y filtra con el guardrail que nunca se recete un tratamiento. Con `INTEGRACION_IA=simulada` no hay IA real: el cuestionario se da por completo y se pasa a pedir las fotos. Con `real` conduce Bedrock (ver «Pipeline de imágenes e identificación»). Si la IA no responde tras los reintentos (`IA_REINTENTOS`), el bot pide perdón y sigue con las fotos: el técnico revisa el caso.
- Un número desconocido se registra con consentimiento (P-B08); uno registrado desde el panel debe aceptar el tratamiento de sus datos (D-12). El texto del aviso es provisional (P-B07).
- Una finca sin cultivos se completa desde WhatsApp (nombre, tipo, especies). Con varias especies en el cultivo, cada consulta es de una sola (P-10).
- Fotos: hasta 3 por tipo y 12 en total; mínimo 1 útil. Las valida el pipeline de imágenes (simulado con `INTEGRACION_IA=simulada`). Un caso llega a **Pendiente de revisión** solo con el cuestionario completo y al menos una foto válida.
- `/salir` pide confirmación y, si confirma, borra el caso con todos sus datos y fotos. Cada mensaje reinicia el conteo de inactividad; responder tras la advertencia de 48 h retoma donde se quedó.
- Todos los textos del bot están en `app/modules/orchestrator/textos.py`.

## Pipeline de imágenes e identificación

Con `INTEGRACION_IA=real` y `INTEGRACION_WHATSAPP=real` el consumidor usa los clientes reales (`construir_orquestador` en `app/workers/sqs_consumer.py`); con `simulada` todo sigue funcionando sin salir a internet.

**Fotos** (`app/modules/image_pipeline/pipeline.py`): descarga de WhatsApp (Graph API, en dos pasos y con el token) → formato y tamaño del **original** (JPEG o PNG, máximo 5 MB: es lo que Rekognition acepta como bytes) → brillo y nitidez → moderación y dominio con Rekognition (sin contenido inapropiado y que sea una planta) → WebP sin metadatos EXIF y con la orientación corregida → `s3://<bucket>/evidencias/<año>/<mes>/<id>.webp`. Una foto rechazada no se guarda y se le dice al productor por qué. Si falla un servicio (Rekognition, S3), el error sube y el consumidor reintenta el mensaje.

**Identificación preliminar** (`app/modules/identification/`): al enviar el caso se busca en `conocimiento_embeddings` (pgvector, distancia coseno) el conocimiento más cercano a los síntomas (RAG) y se le pasa a la IA junto con las respuestas y las fotos. La IA estima el *porcentaje de coincidencia de síntomas* por categoría y `reglas.py` decide el resultado: **identificado** si una categoría llega a `UMBRAL_COINCIDENCIA` (50); **ambiguo** si dos llegan y se llevan menos de `UMBRAL_AMBIGUO` (15) puntos (con 15 exactos ya hay ganadora); **no identificado** si ninguna llega. Siempre es preliminar. Todo texto de la IA pasa por el guardrail (sin tratamientos ni «confianza», «probabilidad» o «certeza»). Si la IA no responde, el caso llega al panel sin identificación (D-03).

**IA en otra cuenta** (`ia_credenciales.py`): `sts:AssumeRole` sobre `IA_ROLE_ARN` con `IA_EXTERNAL_ID`; las credenciales (1 hora) se renuevan 5 minutos antes de vencer y los clientes de Bedrock y Rekognition se recrean con las nuevas. Modelos: `BEDROCK_MODEL_VISION`, `BEDROCK_MODEL_TEXT`, `BEDROCK_MODEL_EMBED`. Los umbrales de calidad de foto (`FOTO_*`) son **provisionales**: se validan con el técnico de la ANUC.

Esto no se pudo probar contra AWS ni Meta reales: las pruebas usan dobles (clientes falsos, `httpx.MockTransport`, moto). La primera prueba con los servicios reales es parte del despliegue.

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

- **Migraciones y semillas:** `alembic upgrade head` y `python -m app.infrastructure.db.seed`. Con `alembic upgrade head --sql` se revisa el SQL sin conectarse. Cada cambio al modelo se anota en `docs/CAMBIOS_MODELO.md`.
- **Semillas v2:** 7 estados del caso, 5 categorías (solo Trips es transmisible), 3 especies (gulupa, granadilla, maracuyá) y un único parámetro epidemiológico, el de Trips (3,5 km, 30 días). El número mínimo de casos (3) es **provisional** hasta que el equipo lo defina (P-B02). No se siembran las preguntas del cuestionario: su texto no está en los documentos (P-B10). La semilla es idempotente y se niega a correr si encuentra la semilla genérica anterior; en ese caso recrea la base local con `docker compose down -v`.
- **Objetos SQL:** las 6 vistas, 2 funciones, 6 procedimientos y 6 triggers de la §6.6 (migración `0003`). Los errores de negocio llevan un SQLSTATE propio (`AN001` a `AN005`) y `app/infrastructure/db/procedimientos.py` trae las sentencias de llamada. Detalle y reglas de la evaluación de alertas en `docs/OBJETOS_SQL.md`.
- **Códigos de la API:** `app/core/catalogos_api.py` mapea los nombres guardados («Pendiente de revisión») con los códigos estables (`PENDIENTE_REVISION`). Un código desconocido en la entrada se rechaza; un nombre desconocido en la salida se normaliza y no rompe la respuesta. Los ids de catálogo (`ES003`, `CF004`) nunca van en el código.
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
