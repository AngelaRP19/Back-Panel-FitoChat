#!/bin/bash
# LocalStack ejecuta este script cuando está listo (montado en /etc/localstack/init/ready.d/).
set -euo pipefail

# Cola de mensajes fallidos (DLQ): a ella pasan los mensajes que no se pudieron procesar tras 5 recepciones.
awslocal sqs create-queue   --queue-name mensajes-entrantes-dlq.fifo   --attributes FifoQueue=true,ContentBasedDeduplication=false
DLQ_ARN=$(awslocal sqs get-queue-attributes   --queue-url http://localhost:4566/000000000000/mensajes-entrantes-dlq.fifo   --attribute-names QueueArn --query Attributes.QueueArn --output text)
echo "Cola mensajes-entrantes-dlq.fifo creada ($DLQ_ARN)"

# Cola principal: visibility timeout de 120 s (mayor que el procesamiento máximo del consumidor, 90 s) y paso a la DLQ.
cat > /tmp/atributos-cola.json <<JSON
{
  "FifoQueue": "true",
  "ContentBasedDeduplication": "false",
  "VisibilityTimeout": "120",
  "RedrivePolicy": "{\"deadLetterTargetArn\":\"${DLQ_ARN}\",\"maxReceiveCount\":\"5\"}"
}
JSON
awslocal sqs create-queue --queue-name mensajes-entrantes.fifo --attributes file:///tmp/atributos-cola.json
echo "Cola mensajes-entrantes.fifo creada"

# Bucket de evidencias: privado y con el versionado activado, como el de AWS.
awslocal s3api create-bucket --bucket anuc-evidencias
awslocal s3api put-bucket-versioning --bucket anuc-evidencias --versioning-configuration Status=Enabled
echo "Bucket anuc-evidencias creado con versionado"

# Estado de las conversaciones del chatbot (D-15): un documento por teléfono, con TTL en `expira_en`.
awslocal dynamodb create-table \
  --table-name conversaciones \
  --attribute-definitions AttributeName=telefono,AttributeType=S \
  --key-schema AttributeName=telefono,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
awslocal dynamodb update-time-to-live --table-name conversaciones \
  --time-to-live-specification Enabled=true,AttributeName=expira_en
echo "Tabla conversaciones creada"

# Documentación fitosanitaria (D-18): metadatos por id; el PDF va al bucket de evidencias con el prefijo documentos/.
awslocal dynamodb create-table \
  --table-name documentos \
  --attribute-definitions AttributeName=id_documento,AttributeType=S \
  --key-schema AttributeName=id_documento,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
echo "Tabla documentos creada"

# Consultas guardadas de los reportes (D-19), sin envío programado (D-21).
awslocal dynamodb create-table \
  --table-name consultas-reportes \
  --attribute-definitions AttributeName=id_consulta,AttributeType=S \
  --key-schema AttributeName=id_consulta,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
echo "Tabla consultas-reportes creada"

# Notificaciones de la campana (D-20): por cuenta y fecha, con TTL de 30 días en `expira_en`. Va de última: el
# healthcheck de docker-compose espera a que exista.
awslocal dynamodb create-table \
  --table-name notificaciones \
  --attribute-definitions AttributeName=destinatario,AttributeType=S AttributeName=fecha_id,AttributeType=S \
  --key-schema AttributeName=destinatario,KeyType=HASH AttributeName=fecha_id,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST
awslocal dynamodb update-time-to-live --table-name notificaciones \
  --time-to-live-specification Enabled=true,AttributeName=expira_en
echo "Tabla notificaciones creada"
