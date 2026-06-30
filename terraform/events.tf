# Schedules the nightly batch prediction job.
resource "aws_cloudwatch_event_rule" "nightly_predictions" {
  name                = "${var.project_name}-nightly-predictions"
  schedule_expression = "cron(0 1 * * ? *)"
  tags                = local.common_tags
}

# Schedules monthly model retraining.
resource "aws_cloudwatch_event_rule" "monthly_training" {
  name                = "${var.project_name}-monthly-training"
  schedule_expression = "cron(0 2 1 * ? *)"
  tags                = local.common_tags
}