# AWS region used for all resources.
variable "aws_region" {
  type = string
}

# Common project name used for resource naming and tagging.
variable "project_name" {
  type = string
}

# Deployment environment (e.g. dev, test, prod).
variable "environment" {
  type = string
}

# Name of the S3 bucket storing application assets.
variable "bucket_name" {
  type = string
}

# PostgreSQL database name.
variable "db_name" {
  type = string
}

# PostgreSQL administrator username.
variable "db_username" {
  type = string
}

# Default training configuration stored in S3.
variable "train_config_s3_key" {
  type = string
}

# Default batch prediction configuration stored in S3.
variable "batch_prediction_config_s3_key" {
  type = string
}