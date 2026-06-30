# Creates the ECS cluster used by all Fargate workloads.
resource "aws_ecs_cluster" "cluster" {
  name = "${var.project_name}-cluster"
  tags = local.common_tags
}

# Allows ECS to pull images and write logs for tasks.
resource "aws_iam_role" "ecs_task_execution" {
  name = "${var.project_name}-ecs-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

# Attaches the AWS-managed execution policy for ECS tasks.
resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Runtime role assumed by the application containers.
resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

# Grants application containers access to S3, RDS credentials and task spawning.
resource "aws_iam_role_policy" "ecs_task_app_permissions" {
  name = "${var.project_name}-ecs-task-app-permissions"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.refund_bucket.arn,
          "${aws_s3_bucket.refund_bucket.arn}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_db_instance.postgres.master_user_secret[0].secret_arn
      },
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

# Defines the training Fargate task.
resource "aws_ecs_task_definition" "train" {
  family                   = "${var.project_name}-train"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 2048
  memory                   = 4096
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "train"
    image     = "${aws_ecr_repository.refund_repo.repository_url}:train"
    essential = true
    command   = ["python", "-m", "ml.train"]

    environment = [
      for k, v in local.app_environment_base : {
        name  = k
        value = v
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs_logs.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "train"
      }
    }
  }])

  tags = local.common_tags
}

# Defines the batch prediction Fargate task.
resource "aws_ecs_task_definition" "batch" {
  family                   = "${var.project_name}-batch"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "batch"
    image     = "${aws_ecr_repository.refund_repo.repository_url}:batch"
    essential = true
    command   = ["python", "-m", "ml.batch_predict"]

    environment = [
      for k, v in local.app_environment_base : {
        name  = k
        value = v
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs_logs.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "batch"
      }
    }
  }])

  tags = local.common_tags
}

# Defines the API Fargate task.
resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project_name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = "${aws_ecr_repository.refund_repo.repository_url}:api"
    essential = true
    command   = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }],

    environment = [
      for k, v in merge(local.app_environment_base, {
        ECS_CLUSTER_NAME       = aws_ecs_cluster.cluster.name
        TRAIN_TASK_DEFINITION  = aws_ecs_task_definition.train.arn
        BATCH_TASK_DEFINITION  = aws_ecs_task_definition.batch.arn
        ECS_SUBNET_IDS         = join(",", data.aws_subnets.default.ids)
        ECS_SECURITY_GROUP_IDS = aws_security_group.ecs_tasks.id
        }) : {
        name  = k
        value = v
      }
    ],

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs_logs.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }])

  tags = local.common_tags
}

# Keeps one API task running behind the load balancer.
resource "aws_ecs_service" "api" {
  name            = "${var.project_name}-api"
  cluster         = aws_ecs_cluster.cluster.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.api_http]

  tags = local.common_tags
}