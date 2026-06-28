terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  app_environment = {
    AWS_REGION           = var.aws_region
    S3_BUCKET            = aws_s3_bucket.refund_bucket.bucket
    CLOUDWATCH_LOG_GROUP = aws_cloudwatch_log_group.ecs_logs.name

    POSTGRES_SECRET_ARN = aws_db_instance.postgres.master_user_secret[0].secret_arn
    POSTGRES_HOST       = aws_db_instance.postgres.address
    POSTGRES_PORT       = tostring(aws_db_instance.postgres.port)
    POSTGRES_DB_NAME    = var.db_name

    TRAIN_CONFIG_S3_KEY            = var.train_config_s3_key
    BATCH_PREDICTION_CONFIG_S3_KEY = var.batch_prediction_config_s3_key
    DATASETS_PREFIX                = "datasets/"
    MODELS_PREFIX                  = "models/"
    METADATA_PREFIX                = "metadata/"
    CONFIGS_PREFIX                 = "configs/"
    INCOMING_IMAGES_PREFIX         = "incoming-images/"
  }
}

resource "aws_resourcegroups_group" "refund_group" {
  name = var.project_name

  resource_query {
    query = jsonencode({
      ResourceTypeFilters = ["AWS::AllSupported"]
      TagFilters = [{
        Key    = "Project"
        Values = [var.project_name]
      }]
    })
  }
}

resource "aws_s3_bucket" "refund_bucket" {
  bucket = var.bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "refund_bucket" {
  bucket = aws_s3_bucket.refund_bucket.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_db_instance" "postgres" {
  identifier                  = "${var.project_name}-db"
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  db_name                     = var.db_name
  username                    = var.db_username
  manage_master_user_password = true
  publicly_accessible         = true
  vpc_security_group_ids      = [aws_security_group.rds.id]
  skip_final_snapshot         = true

  tags = local.common_tags
}

resource "aws_default_vpc" "default" {
  tags = local.common_tags
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg"
  description = "Allow PostgreSQL access"
  vpc_id      = aws_default_vpc.default.id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_ecr_repository" "refund_repo" {
  name = var.project_name
  tags = local.common_tags
}

resource "aws_ecs_cluster" "cluster" {
  name = "${var.project_name}-cluster"
  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "ecs_logs" {
  name              = "/ecs/${var.project_name}"
  retention_in_days = 14
  tags              = local.common_tags
}

resource "aws_cloudwatch_event_rule" "nightly_predictions" {
  name                = "${var.project_name}-nightly-predictions"
  schedule_expression = "cron(0 1 * * ? *)"
  tags                = local.common_tags
}

resource "aws_cloudwatch_event_rule" "monthly_training" {
  name                = "${var.project_name}-monthly-training"
  schedule_expression = "cron(0 2 1 * ? *)"
  tags                = local.common_tags
}