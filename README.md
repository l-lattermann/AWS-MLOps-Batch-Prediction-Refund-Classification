# Refund Classification

**Architecture Diagram**

https://miro.com/app/board/uXjVHFPVJHY=/

---

# Setup

## Prerequisites

- Python 3.11
- Terraform
- AWS CLI

## Python

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## AWS

Create an AWS IAM user (or least-privilege role) and configure the AWS CLI.

```bash
aws configure
```

---

# Environment Variables & Secrets

Terraform creates the AWS infrastructure (RDS, S3, ECS, CloudWatch, ECR, ...).

## RDS

`manage_master_user_password = true` lets AWS generate and rotate the PostgreSQL password automatically.

- Password is stored in **AWS Secrets Manager**
- RDS references the secret
- Terraform exposes only the **Secret ARN**

## ECS

The ECS Task Definition injects:

**Environment variables**

- AWS region
- S3 bucket
- configuration paths
- storage prefixes
- other non-sensitive settings

**Secrets**

- `POSTGRES_SECRET_ARN`

## Application

At runtime the application:

1. Reads the environment variables.
2. Uses `POSTGRES_SECRET_ARN` to retrieve the database credentials via `boto3`.
3. Connects to PostgreSQL.

The database password is never stored in the repository, `.env` files, or application code.

---

# Infrastructure Deployment

```bash
./scripts/deploy_aws_resources.sh
```

This script:

- initializes Terraform
- creates or updates all AWS resources
- exports Terraform outputs to

```
terraform/infrastructure.json
```

`terraform/infrastructure.json` is generated automatically and used by local setup scripts such as `bootstrap_aws.py`.

---

# Database Initialization

```bash
python -m scripts.db_init
```

This script:

- loads the local environment variables
- retrieves the PostgreSQL credentials from AWS Secrets Manager
- connects to the RDS instance
- executes `sql/01_schema.sql`

Run this once after deploying the infrastructure.

---
# AWS Bootstrap

```bash
python -m scripts.bootstrap_aws
```

This script:

- reads `terraform/infrastructure.json`
- uploads everything in `config/` to

```text
s3://<bucket>/config/
```

- uploads every dataset in `data/` to

```text
s3://<bucket>/datasets/<dataset_name>/v1/
```

while preserving the local directory structure, e.g.

```text
train/
validation/
test/
```

including all class subdirectories.

- upserts one entry per dataset version into the `datasets` table in RDS
- uploads trained models from `ml/models/` to

```text
s3://<bucket>/models/<model_version>/
```

- uploads both the `.pth` model file and the corresponding `_metadata.json`
- upserts model metadata into the `models` table in RDS
- uploads validation images again as mock production input to

```text
s3://<bucket>/incoming-images/
```

using flat generic filenames like:

```text
incoming_00001.jpg
incoming_00002.jpg
```

- upserts one entry per incoming image into the `images` table in RDS
- sets incoming image status to `PENDING`
- uses deterministic IDs based on the S3 key
- is idempotent: rerunning the script skips existing S3 objects and updates existing RDS metadata instead of creating duplicate database entries

The dataset directory name is used as the dataset identifier.

---


# Initial Setup Order

1. Create the Python environment
2. Configure AWS CLI
3. Deploy the infrastructure
4. Initialize the database
5. Bootstrap S3
6. Run the API, training, or batch prediction jobs