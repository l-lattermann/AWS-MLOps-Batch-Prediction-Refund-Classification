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

#################################
# S3
#################################

resource "aws_s3_bucket" "refund_bucket" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_versioning" "refund_bucket" {
  bucket = aws_s3_bucket.refund_bucket.id

  versioning_configuration {
    status = "Enabled"
  }
}

#################################
# RDS PostgreSQL
#################################

resource "aws_db_instance" "postgres" {
  identifier             = "refund-classification-db"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = "db.t4g.micro"
  allocated_storage      = 20

  db_name  = "refunddb"
  username = var.db_username
  password = var.db_password

  publicly_accessible = true
  skip_final_snapshot = true
}

#################################
# ECR
#################################

resource "aws_ecr_repository" "refund_repo" {
  name = "refund-classification"
}

#################################
# ECS Cluster
#################################

resource "aws_ecs_cluster" "cluster" {
  name = "refund-cluster"
}

#################################
# CloudWatch Logs
#################################

resource "aws_cloudwatch_log_group" "ecs_logs" {
  name              = "/ecs/refund-classification"
  retention_in_days = 14
}

#################################
# EventBridge
#################################

resource "aws_cloudwatch_event_rule" "nightly_predictions" {
  name                = "nightly-predictions"
  schedule_expression = "cron(0 1 * * ? *)"
}

resource "aws_cloudwatch_event_rule" "monthly_training" {
  name                = "monthly-training"
  schedule_expression = "cron(0 2 1 * ? *)"
}