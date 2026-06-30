#!/usr/bin/env bash
set -euo pipefail

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

python -m pytest tests/test_aws_infra.py
python -m pytest tests/test_pred.py