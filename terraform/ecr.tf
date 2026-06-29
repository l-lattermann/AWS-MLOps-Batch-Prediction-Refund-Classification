resource "aws_ecr_repository" "refund_repo" {
  name = var.project_name
  tags = local.common_tags
}
