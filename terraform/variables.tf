variable "aws_region" {
  default = "eu-central-1"
}

variable "bucket_name" {
  default = "refund-classification-bucket"
}

variable "db_username" {
  sensitive = true
}

variable "db_password" {
  sensitive = true
}
