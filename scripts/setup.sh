#!/bin/bash
set -e

cd terraform
terraform destroy -auto-approve || true
cd ..

./scripts/deploy_aws_resources.sh

python3 scripts/export_envs.py

set -a
source .env
set +a

python -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

echo "Waiting for RDS..."
until python -m scripts.db_init --check; do
    sleep 5
done

python -m scripts.db_init
python -m scripts.bootstrap_aws


./scripts/build_images.sh
./scripts/push_images.sh

python -m pytest tests/test_aws_infra.py