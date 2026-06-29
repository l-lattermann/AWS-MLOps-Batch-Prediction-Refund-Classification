#!/bin/bash
set -e

set -a
source .env
set +a

docker build --platform linux/amd64 -f docker/Dockerfile.api -t refund-api .
docker build --platform linux/amd64 -f docker/Dockerfile.train -t refund-train .
docker build --platform linux/amd64 -f docker/Dockerfile.batch -t refund-batch .

echo "Images built successfully."
