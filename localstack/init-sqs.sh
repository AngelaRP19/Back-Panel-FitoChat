#!/bin/bash
# LocalStack ejecuta este script cuando está listo (montado en /etc/localstack/init/ready.d/).
set -euo pipefail

awslocal sqs create-queue \
  --queue-name mensajes-entrantes.fifo \
  --attributes FifoQueue=true,ContentBasedDeduplication=false

echo "Cola mensajes-entrantes.fifo creada"
