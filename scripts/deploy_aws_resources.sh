#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env if present
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    source "$ROOT_DIR/.env"
    set +a
fi

: "${INFRASTRUCTURE_OUTPUT_PATH:?INFRASTRUCTURE_OUTPUT_PATH is not set}"

cd "$ROOT_DIR/terraform"

terraform init
terraform apply

terraform output -json > "$ROOT_DIR/$INFRASTRUCTURE_OUTPUT_PATH"
