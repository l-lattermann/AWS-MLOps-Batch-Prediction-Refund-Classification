#!/bin/bash
set -e

set -a
source .env
set +a

REPO_URL="$(terraform -chdir=terraform output -raw ecr_repository_url)"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REPO_URL"

docker tag refund-api "${REPO_URL}:api"
docker tag refund-train "${REPO_URL}:train"
docker tag refund-batch "${REPO_URL}:batch"

docker push "${REPO_URL}:api"
docker push "${REPO_URL}:train"
docker push "${REPO_URL}:batch"

echo "Pushed:"
echo "${REPO_URL}:api"
echo "${REPO_URL}:train"
echo "${REPO_URL}:batch"
