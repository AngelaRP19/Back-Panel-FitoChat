# Manual de instalación y operación · FitoChat ANUC Zetaquirá

Sistema de identificación fitosanitaria temprana: un chatbot de WhatsApp para los productores y un panel web para los
líderes técnicos y los técnicos de la ANUC. Este manual explica cómo instalarlo en un equipo de desarrollo, cómo se
despliega en AWS y cómo se opera. El uso del panel y del chatbot está en el **Manual de usuario**
(`Panel-ANUC-FitoChat/docs/MANUAL_USUARIO.md`).

Ningún valor secreto (contraseñas, tokens, claves) aparece en este manual ni en los repositorios: en local van en
`.env` y en AWS en Secrets Manager.

## 1. Componentes

| Componente | Repositorio | Tecnología | Dónde corre |
|---|---|---|---|
| API del panel y webhook de WhatsApp | `Back-Panel-FitoChat` | Python 3.12, FastAPI | ECS Fargate, servicio `anuc-backend-service`, detrás del ALB |
| Consumidor de mensajes (chatbot) | `Back-Panel-FitoChat` (misma imagen) | `python -m app.workers.sqs_consumer` | ECS Fargate, servicio `anuc-consumer-svc` |
| Jobs programados | `Back-Panel-FitoChat` (misma imagen) | `python -m app.jobs.run <nombre>` | EventBridge Scheduler → tarea ECS |
| Panel web | `Panel-ANUC-FitoChat` | React, TypeScript, Vite | Bucket S3 `anuc-panel-web-fitochat` + CloudFront |
| Base de datos | — | PostgreSQL 16 con PostGIS y pgvector | Amazon RDS |
| Cola de mensajes | — | SQS FIFO `mensajes-entrantes.fifo` (+ cola de fallidos `-dlq`) | AWS |
| Estado del chat, notificaciones, documentos, consultas | — | DynamoDB `anuc-conversaciones`, `anuc-notificaciones`, `anuc-documentos`, `anuc-consultas-reportes` | AWS |
| Fotos y documentos | — | S3 `anuc-evidencias-fitochat` | AWS |
| Cuentas del panel | — | Amazon Cognito (User Pool) | AWS |
| IA (fotos e identificación) | — | Rekognition y Bedrock, en otra cuenta (rol `anuc-ia-invoker`) | AWS |

Todo está en la región `us-east-1` (Estados Unidos). El aviso de privacidad del chatbot lo informa y pide la
autorización del productor (Ley 1581 de 2012, art. 26).

## 2. Instalación en un equipo de desarrollo

### 2.1 Requisitos

- Git.
- Docker Desktop (para PostgreSQL y LocalStack).
- Python 3.12 (backend).
- Node.js 24 (panel; la versión está en `.nvmrc`) y npm 11.

### 2.2 Backend

```bash
git clone https://github.com/AngelaRP19/Back-Panel-FitoChat.git
cd Back-Panel-FitoChat
docker compose up -d db localstack
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env            # Linux/macOS: cp .env.example .env
alembic upgrade head
python -m app.infrastructure.db.seed          # catálogos (estados, especies, categorías…)
python -m app.infrastructure.db.seed_demo     # opcional: datos de demostración
uvicorn app.main:app --reload                 # API en http://localhost:8000
python -m app.workers.sqs_consumer            # en otra terminal: el chatbot
```

Con `INTEGRACION_WHATSAPP=simulada` e `INTEGRACION_IA=simulada` (valores por defecto) no sale nada a Meta ni a la IA:
sirve para desarrollar y para las pruebas. Las variables de entorno están documentadas en el `README.md`.

Comprobar la instalación:

```bash
curl http://localhost:8000/salud                 # {"status":"ok"}
pytest -p no:cacheprovider                        # pruebas (necesitan la base del compose)
ruff check . && ruff format --check . && mypy app
```

### 2.3 Panel web

```bash
git clone https://github.com/AngelaRP19/Panel-ANUC-FitoChat.git
cd Panel-ANUC-FitoChat
npm ci
cp .env.example .env.local        # VITE_USE_MOCKS=true: trabaja sin backend ni Cognito
npm run dev                        # http://localhost:5173
```

Con `VITE_USE_MOCKS=true` el panel simula la API y Cognito; las cuentas de demostración están en su `README.md`. Con
`false`, el panel llama a la API (`/api` se redirige a `http://localhost:8000`) y necesita `VITE_COGNITO_USER_POOL_ID`
y `VITE_COGNITO_CLIENT_ID`.

Comprobar: `npm run lint`, `npm run typecheck`, `npm run coverage` (exige 80 % de cobertura) y `npm run build`.

## 3. Despliegue en AWS

### 3.1 Infraestructura (se crea una sola vez)

1. **Red**: VPC con subredes públicas (ALB) y privadas (ECS, RDS); grupos de seguridad que solo dejan entrar al ALB
   desde CloudFront y a RDS desde las tareas de ECS.
2. **RDS PostgreSQL 16** con las extensiones `postgis` y `vector`; la contraseña la administra Secrets Manager (rotación
   automática).
3. **SQS**: `mensajes-entrantes.fifo` con su cola de fallidos `mensajes-entrantes-dlq.fifo` (10 recepciones, *visibility
   timeout* de 120 s).
4. **DynamoDB**: las cuatro tablas `anuc-*`, bajo demanda y con PITR (claves en el `README.md`).
5. **S3**: `anuc-evidencias-fitochat` (privado, versionado, cifrado) y `anuc-panel-web-fitochat` (solo lo lee
   CloudFront).
6. **Cognito**: User Pool del panel con un cliente de aplicación sin secreto.
7. **ECR**: repositorio `anuc-backend`.
8. **ECS**: clúster `anuc-cluster`, definiciones `anuc-backend` y `anuc-consumer` (misma imagen, otro comando) y los
   dos servicios. Rol de la tarea `anuc-permisos-app` con los permisos mínimos que lista el `README.md`.
9. **Secrets Manager**: `anuc/whatsapp` (token de verificación, *app secret*, token de acceso e id del número) y
   `anuc/ia-cuenta` (rol y *external id* de la cuenta de IA).
10. **ALB** con una regla que exige la cabecera `X-Origin-Verify` (solo CloudFront la conoce) y *health check* en
    `/salud`.
11. **CloudFront**: `/api/*`, `/webhook/*` y `/salud` van al ALB sin caché; lo demás, al bucket del panel con la
    función `anuc-spa-rewrite`.
12. **EventBridge Scheduler** (zona `America/Bogota`): `anuc-job-advertencia-inactividad` y
    `anuc-job-descarte-inactividad` cada 15 min, `anuc-job-retencion` a las 03:00 y `anuc-job-evaluar-alertas` a las
    03:30.
13. **CloudWatch**: logs `/ecs/anuc-backend` y las alarmas del apartado 4.2.

### 3.2 Publicar una versión del backend

Siempre desde `main` (la rama de despliegue). Cada versión tiene su etiqueta de imagen y su etiqueta de git (`v1.10`,
`v1.11`…), y nunca se reutiliza una etiqueta.

1. Construir y subir la imagen `linux/amd64`, con el commit como etiqueta `org.opencontainers.image.revision`:
   ```bash
   docker buildx build --platform linux/amd64 \
     --label org.opencontainers.image.revision=$(git rev-parse HEAD) \
     -t <cuenta>.dkr.ecr.us-east-1.amazonaws.com/anuc-backend:<versión> --push .
   ```
2. Si la versión trae migraciones, correr `alembic upgrade head` como tarea ECS puntual con la definición
   `anuc-backend`.
3. Registrar una revisión nueva de `anuc-backend` y de `anuc-consumer` que solo cambie la imagen.
4. Apuntar los cuatro schedules `anuc-job-*` a la revisión nueva de `anuc-backend` (están fijados a una revisión).
5. `aws ecs update-service` de los dos servicios y esperar `aws ecs wait services-stable`.
6. Verificar: `/salud` responde 200, `/api/v1/me` sin token responde 401 y los logs no muestran errores.
7. Etiquetar el commit en git (`git tag v<versión>` y `git push origin v<versión>`).

### 3.3 Publicar el panel

```bash
npm ci
npm run build                     # con .env.production: VITE_USE_MOCKS=false y los ids de Cognito
aws s3 sync dist/assets s3://anuc-panel-web-fitochat/assets --cache-control "public,max-age=31536000,immutable"
aws s3 cp dist/index.html s3://anuc-panel-web-fitochat/index.html --cache-control no-cache --content-type text/html
aws cloudfront create-invalidation --distribution-id <id> --paths /index.html
```

`.env.production` no se versiona (está en `.gitignore`); sus valores no son secretos, pero son propios del ambiente.

### 3.4 WhatsApp (Meta)

En la aplicación de Meta, el webhook apunta a `https://<dominio de CloudFront>/webhook/whatsapp`, con el token de
verificación de `anuc/whatsapp`, suscrito a `messages`. El backend valida la firma `X-Hub-Signature-256` de cada
llamada.

### 3.5 Primer uso

1. Entrar al panel: sin líderes registrados aparece **Primer uso** para crear el primer líder técnico.
2. El líder registra a los técnicos y a los productores autorizados (**solo los números registrados en el panel
   pueden usar el chatbot**).
3. El productor escribe al número de WhatsApp y acepta el aviso de privacidad.

## 4. Operación

### 4.1 Registros

Logs JSON en CloudWatch (`/ecs/anuc-backend`), con identificador de correlación y sin datos personales (los celulares
van enmascarados). Mensajes útiles: «Respuesta enviada», «Acceso al chatbot rechazado», «Foto rechazada», «Foto de otra
especie» (con lo que vio el modelo de visión), «Código de recuperación enviado», «Cognito no envió el código de
recuperación», «Meta no entregó un mensaje», «No se pudo procesar el mensaje».

Las fotos pasan dos filtros de IA: Rekognition (contenido inapropiado y si es una planta) y el modelo de visión de
Bedrock (`BEDROCK_MODEL_VISION`), que rechaza las que muestran claramente otra especie y no una Passiflora.

### 4.2 Alarmas (tema SNS `anuc-alarmas`)

| Alarma | Se dispara cuando |
|---|---|
| `anuc-alb-5xx` | El backend responde 5 o más errores 5xx en 5 minutos |
| `anuc-dlq-mensajes` | Hay algún mensaje de WhatsApp en la cola de fallidos (no se pudo procesar tras 10 intentos) |
| `anuc-api-sin-tareas` | El servicio de la API se queda sin tareas sanas detrás del ALB |
| `anuc-tareas-fallidas` (regla de EventBridge) | Una tarea de ECS (API, consumidor o job) se detiene con un código de salida distinto de 0 |

Para recibir los avisos, suscribir un correo al tema `anuc-alarmas` (`aws sns subscribe --protocol email`) y confirmar
el enlace que llega.

### 4.3 Tareas habituales

- **Mensaje en la cola de fallidos**: leer el error en los logs («No se pudo procesar el mensaje»), corregir y, si
  todavía tiene sentido responderle al productor, devolverlo a la cola principal (*redrive*).
- **Cambiar la cabecera `X-Origin-Verify`**: agregar el valor nuevo a la regla del ALB (acepta varios), cambiarlo en el
  origen de CloudFront, esperar el despliegue y quitar el valor viejo del ALB. Así no hay corte.
- **Credenciales de la base**: Secrets Manager las rota solo; las tareas nuevas leen el valor vigente al arrancar.

## 5. Solución de problemas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El panel muestra «Servicio no disponible» | La API no tiene tareas sanas | Ver eventos del servicio en ECS y los logs |
| El productor no recibe respuesta | Su número no está registrado o está deshabilitado (recibe un aviso), o el mensaje está en la cola de fallidos | Revisar el productor en el panel y la alarma `anuc-dlq-mensajes` |
| Un usuario del panel no recibe el código de recuperación | Spam, límite diario del correo por defecto de Cognito (50) o la cuenta nunca entró | Buscar «Código de recuperación enviado» o «Cognito no envió…» en los logs; para más volumen, configurar SES en el User Pool |
| Llegan avisos tarde | Fuera de la ventana de 24 h de WhatsApp: quedan pendientes hasta que el productor escriba | Normal sin plantillas aprobadas por Meta |
| `alembic` no encuentra la base | `DATABASE_URL` o `DB_*` mal definidas | Revisar `.env` o la definición de la tarea |
| En Windows, `/api/v1/me` falla en local | Falta `--reload` en `uvicorn` | Ver `README.md` |
