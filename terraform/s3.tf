resource "aws_s3_bucket" "refund_bucket" {
  bucket = var.bucket_name
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "refund_bucket" {
  bucket = aws_s3_bucket.refund_bucket.id

  versioning_configuration {
    status = "Enabled"
  }
}
