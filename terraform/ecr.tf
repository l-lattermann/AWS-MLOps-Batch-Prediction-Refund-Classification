# Stores Docker images for the API, training and batch services.
resource "aws_ecr_repository" "refund_repo" {
  name = var.project_name
  tags = local.common_tags
}