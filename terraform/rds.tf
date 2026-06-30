resource "aws_db_instance" "postgres" {
  identifier                  = "${var.project_name}-db"
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  db_name                     = var.db_name
  username                    = var.db_username
  manage_master_user_password = true
  publicly_accessible         = true
  vpc_security_group_ids      = [aws_security_group.rds.id]
  skip_final_snapshot         = true
  copy_tags_to_snapshot       = false

  tags = local.common_tags
}
