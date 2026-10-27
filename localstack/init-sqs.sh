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
