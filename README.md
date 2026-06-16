# Refund Classification MLOps

Architecture:

FastAPI (App Runner)
S3
RDS PostgreSQL
EventBridge
ECS Fargate
CloudWatch

Workflow:

Upload Image
-> S3

Nightly Prediction
-> EventBridge
-> ECS Fargate
-> RDS

Monthly Retraining
-> EventBridge
-> ECS Fargate
-> S3 Model Registry
