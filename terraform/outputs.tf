output "s3_bucket" {
  value = aws_s3_bucket.refund_bucket.bucket
}

output "rds_endpoint" {
  value = aws_db_instance.postgres.address
}

output "ecr_repository_url" {
  value = aws_ecr_repository.refund_repo.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.cluster.name
}