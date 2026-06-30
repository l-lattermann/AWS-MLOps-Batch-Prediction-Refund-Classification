# Exposes the S3 bucket used for datasets, models and images.
output "s3_bucket" {
  value = aws_s3_bucket.refund_bucket.bucket
}

# Exposes the ECR repository URL for container image pushes.
output "ecr_repository_url" {
  value = aws_ecr_repository.refund_repo.repository_url
}

# Exposes the ECS cluster name.
output "ecs_cluster_name" {
  value = aws_ecs_cluster.cluster.name
}

# Exposes the CloudWatch log group used by ECS tasks.
output "cloudwatch_log_group" {
  value = aws_cloudwatch_log_group.ecs_logs.name
}

# Exposes the AWS Resource Group name.
output "resource_group_name" {
  value = aws_resourcegroups_group.refund_group.name
}

# Exposes the Secrets Manager ARN storing the database credentials.
output "postgres_secret_arn" {
  value = aws_db_instance.postgres.master_user_secret[0].secret_arn
}

# Exposes all application environment variables for local scripts.
output "app_environment" {
  value = local.app_environment
}

# Exposes the training task definition ARN.
output "train_task_definition" {
  value = aws_ecs_task_definition.train.arn
}

# Exposes the batch prediction task definition ARN.
output "batch_task_definition" {
  value = aws_ecs_task_definition.batch.arn
}

# Exposes the ECS task security group ID.
output "ecs_task_security_group_id" {
  value = aws_security_group.ecs_tasks.id
}

# Exposes the default subnet IDs used by ECS tasks.
output "default_subnet_ids" {
  value = data.aws_subnets.default.ids
}

# Exposes the API task definition ARN.
output "api_task_definition" {
  value = aws_ecs_task_definition.api.arn
}

# Exposes the ECS API service name.
output "api_service_name" {
  value = aws_ecs_service.api.name
}

# Exposes the public API endpoint.
output "api_url" {
  value = "http://${aws_lb.api.dns_name}"
}