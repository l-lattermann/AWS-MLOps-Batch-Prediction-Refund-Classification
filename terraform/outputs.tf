output "s3_bucket" {
  value = aws_s3_bucket.refund_bucket.bucket
}

output "rds_endpoint" {
  value = aws_db_instance.postgres.address
}

output "rds_port" {
  value = aws_db_instance.postgres.port
}

output "ecr_repository_url" {
  value = aws_ecr_repository.refund_repo.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.cluster.name
}

output "cloudwatch_log_group" {
  value = aws_cloudwatch_log_group.ecs_logs.name
}

output "resource_group_name" {
  value = aws_resourcegroups_group.refund_group.name
}

output "postgres_secret_name" {
  value = aws_secretsmanager_secret.postgres.name
}

output "app_config_secret_name" {
  value = aws_secretsmanager_secret.app_config.name
}