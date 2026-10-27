#!/bin/bash
# LocalStack ejecuta este script cuando está listo (montado en /etc/localstack/init/ready.d/).
set -euo pipefail

awslocal sqs create-queue \
  --queue-name mensajes-entrantes.fifo \
  --attributes FifoQueue=true,ContentBasedDeduplication=false
echo "Cola mensajes-entrantes.fifo creada"

# Bucket de evidencias: privado y con el versionado activado, como el de AWS.
awslocal s3api create-bucket --bucket anuc-evidencias
awslocal s3api put-bucket-versioning --bucket anuc-evidencias --versioning-configuration Status=Enabled
echo "Bucket anuc-evidencias creado con versionado"
