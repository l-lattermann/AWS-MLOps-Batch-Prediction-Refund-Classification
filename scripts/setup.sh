#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -d ".venv" ]; then
    rm -rf .venv
fi

python -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cd terraform
terraform destroy -auto-approve || true
cd ..

./scripts/deploy_aws_resources.sh

./scripts/build_images.sh
./scripts/push_images.sh

python -m scripts.export_envs

set -a
source .env
set +a

echo "Waiting for RDS..."
sleep 60

python -m scripts.db_init
python -m scripts.bootstrap_aws

printf "\nTesting if infrastructure was deployed correctly and is online...\n"
python -m pytest tests/test_aws_infra.py

printf "\nTesting if batch prediction runs and metadata upserts to RDS work...\nBe patient, this will take around 5 minutes!\n"
python -m pytest tests/test_pred.py

printf "\nTesting if training runs and metadata upserts to RDS work...\nBe patient, this will take around 30 minutes!\n"
python -m pytest tests/test_train.py

printf "\nTesting if the database is fully populated after all functionalities have been executed...\n"
python -m pytest tests/test_db_upserts.py