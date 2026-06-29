resource "aws_iam_role" "eventbridge_ecs" {
  name = "${var.project_name}-eventbridge-ecs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "eventbridge_ecs" {
  name = "${var.project_name}-eventbridge-ecs"
  role = aws_iam_role.eventbridge_ecs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        Resource = [
          aws_ecs_task_definition.train.arn,
          aws_ecs_task_definition.batch.arn
        ]
      },
      {
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.ecs_task_execution.arn,
          aws_iam_role.ecs_task.arn
        ]
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "nightly_predictions" {
  rule     = aws_cloudwatch_event_rule.nightly_predictions.name
  arn      = aws_ecs_cluster.cluster.arn
  role_arn = aws_iam_role.eventbridge_ecs.arn

  ecs_target {
    launch_type         = "FARGATE"
    task_definition_arn = aws_ecs_task_definition.batch.arn
    task_count          = 1

    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.ecs_tasks.id]
      assign_public_ip = true
    }
  }
}

resource "aws_cloudwatch_event_target" "monthly_training" {
  rule     = aws_cloudwatch_event_rule.monthly_training.name
  arn      = aws_ecs_cluster.cluster.arn
  role_arn = aws_iam_role.eventbridge_ecs.arn

  ecs_target {
    launch_type         = "FARGATE"
    task_definition_arn = aws_ecs_task_definition.train.arn
    task_count          = 1

    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.ecs_tasks.id]
      assign_public_ip = true
    }
  }
}
