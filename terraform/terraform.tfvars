# AWS region for the deployment.
aws_region = "eu-central-1"

# General project settings.
project_name = "refund-classification"
environment  = "dev"

# S3 bucket used to store datasets, models, configs and images.
bucket_name = "refund-classification-bucket"

# PostgreSQL database configuration.
db_name     = "refunddb"
db_username = "postgres"

# Default configuration files uploaded to S3 during bootstrap.
train_config_s3_key            = "config/train/training_params_mobilenetV3L.yml"
batch_prediction_config_s3_key = "config/pred/batch_prediction.yml"