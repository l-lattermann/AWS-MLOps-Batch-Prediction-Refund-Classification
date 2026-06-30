# Creates the central S3 bucket for datasets, models, configs and images.
resource "aws_s3_bucket" "refund_bucket" {
  bucket = var.bucket_name
  tags   = local.common_tags
}

# Enables object versioning to preserve previous file revisions.
resource "aws_s3_bucket_versioning" "refund_bucket" {
  bucket = aws_s3_bucket.refund_bucket.id

  versioning_configuration {
    status = "Enabled"
  }
}