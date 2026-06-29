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

  app_environment_base = {
    AWS_REGION           = var.aws_region
    S3_BUCKET            = aws_s3_bucket.refund_bucket.bucket
    CLOUDWATCH_LOG_GROUP = aws_cloudwatch_log_group.ecs_logs.name

    POSTGRES_SECRET_ARN = aws_db_instance.postgres.master_user_secret[0].secret_arn
    POSTGRES_HOST       = aws_db_instance.postgres.address
    POSTGRES_PORT       = tostring(aws_db_instance.postgres.port)
    POSTGRES_DB_NAME    = var.db_name

    TRAIN_CONFIG_S3_KEY            = var.train_config_s3_key
    BATCH_PREDICTION_CONFIG_S3_KEY = var.batch_prediction_config_s3_key

    DATASETS_PREFIX        = "datasets/"
    MODELS_PREFIX          = "models/"
    CONFIGS_PREFIX         = "configs/"
    INCOMING_IMAGES_PREFIX = "incoming-images/"
  }

  app_environment_runtime = {
    ECS_CLUSTER_NAME       = aws_ecs_cluster.cluster.name
    TRAIN_TASK_DEFINITION  = aws_ecs_task_definition.train.arn
    BATCH_TASK_DEFINITION  = aws_ecs_task_definition.batch.arn
    ECS_SUBNET_IDS         = join(",", data.aws_subnets.default.ids)
    ECS_SECURITY_GROUP_IDS = aws_security_group.ecs_tasks.id
    API_SERVICE_NAME       = aws_ecs_service.api.name
    API_URL                = "http://${aws_lb.api.dns_name}"
  }

  app_environment = merge(
    local.app_environment_base,
    local.app_environment_runtime
  )
}
