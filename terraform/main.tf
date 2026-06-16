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
}

resource "aws_resourcegroups_group" "refund_group" {
  name = var.project_name

  resource_query {
    query = jsonencode({
      ResourceTypeFilters = ["AWS::AllSupported"]
      TagFilters = [
        {
          Key    = "Project"
          Values = [var.project_name]
        }
      ]
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
  identifier             = "${var.project_name}-db"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = "db.t4g.micro"
  allocated_storage      = 20
  db_name                = var.db_name
  username               = var.db_username
  password               = var.db_password
  publicly_accessible    = true
  skip_final_snapshot    = true

  tags = local.common_tags
}

resource "aws_secretsmanager_secret" "postgres" {
  name = "${var.project_name}/${var.environment}/postgres"
  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "postgres" {
  secret_id = aws_secretsmanager_secret.postgres.id

  secret_string = jsonencode({
    host     = aws_db_instance.postgres.address
    port     = aws_db_instance.postgres.port
    database = var.db_name
    username = var.db_username
    password = var.db_password
  })
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

resource "aws_secretsmanager_secret" "app_config" {
  name = "${var.project_name}/${var.environment}/app-config"
  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "app_config" {
  secret_id = aws_secretsmanager_secret.app_config.id

  secret_string = jsonencode({
    aws_region                     = var.aws_region
    s3_bucket                      = aws_s3_bucket.refund_bucket.bucket
    cloudwatch_log_group           = aws_cloudwatch_log_group.ecs_logs.name
    ecr_repository_url             = aws_ecr_repository.refund_repo.repository_url
    postgres_secret_name           = aws_secretsmanager_secret.postgres.name
    train_config_s3_key            = var.train_config_s3_key
    batch_prediction_config_s3_key = var.batch_prediction_config_s3_key

    datasets_prefix        = "datasets/"
    models_prefix          = "models/"
    metadata_prefix        = "metadata/"
    configs_prefix         = "configs/"
    incoming_images_prefix = "incoming-images/"
  })
}