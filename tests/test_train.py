"""Integration test verifying that model training runs and stores metadata."""

import os
from pathlib import Path
import time

import boto3
from dotenv import load_dotenv

from storage.rds_connection import get_connection

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

AWS_REGION = os.environ["AWS_REGION"]
ECS_CLUSTER_NAME = os.environ["ECS_CLUSTER_NAME"]
TRAIN_TASK_DEFINITION = os.environ["TRAIN_TASK_DEFINITION"]
ECS_SUBNET_IDS = os.environ["ECS_SUBNET_IDS"].split(",")
ECS_SECURITY_GROUP_IDS = os.environ["ECS_SECURITY_GROUP_IDS"].split(",")
CLOUDWATCH_LOG_GROUP = os.environ.get("CLOUDWATCH_LOG_GROUP", "/ecs/refund-classification")

ecs = boto3.client("ecs", region_name=AWS_REGION)
logs = boto3.client("logs", region_name=AWS_REGION)


def test_training_pipeline():
    """Verify that training completes and writes model metadata to RDS."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM datasets")
            datasets_before = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM training_runs")
            runs_before = cur.fetchone()[0]

    assert datasets_before > 0

    res = ecs.run_task(
        cluster=ECS_CLUSTER_NAME,
        taskDefinition=TRAIN_TASK_DEFINITION,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": ECS_SUBNET_IDS,
                "securityGroups": ECS_SECURITY_GROUP_IDS,
                "assignPublicIp": "ENABLED",
            }
        },
    )

    assert not res.get("failures"), res.get("failures")

    task_arn = res["tasks"][0]["taskArn"]

    timeout = time.time() + 60 * 60

    while True:
        task = ecs.describe_tasks(cluster=ECS_CLUSTER_NAME, tasks=[task_arn])["tasks"][0]
        container = task["containers"][0]

        if container.get("lastStatus") == "STOPPED":
            assert container.get("exitCode", 1) == 0
            break

        assert time.time() < timeout
        time.sleep(15)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT run_id, model_version, dataset_id, dataset_version, status,
                       train_loss, validation_loss, train_accuracy, validation_accuracy,
                       test_accuracy, epochs, batch_size, learning_rate,
                       training_duration_seconds, error_message
                FROM training_runs
                ORDER BY started_at DESC
                LIMIT 1
            """)
            run = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM training_runs")
            runs_after = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM models WHERE active = TRUE")
            active_models = cur.fetchone()[0]

            cur.execute("""
                SELECT s3_model_path
                FROM models
                WHERE model_version = %s
            """, (run[1],))
            model_row = cur.fetchone()

    assert run is not None
    assert run[4] == "SUCCESS"
    assert run[14] is None
    assert run[5] is not None
    assert run[6] is not None
    assert run[7] is not None
    assert run[8] is not None
    assert run[9] is not None
    assert run[10] > 0
    assert run[11] > 0
    assert run[12] > 0
    assert run[13] is not None

    assert runs_after > runs_before
    assert active_models == 1
    assert model_row is not None
    assert model_row[0].startswith("models/")

    streams = logs.describe_log_streams(
        logGroupName=CLOUDWATCH_LOG_GROUP,
        orderBy="LastEventTime",
        descending=True,
        limit=5,
    )["logStreams"]

    assert streams

    text = ""
    for stream in streams:
        events = logs.get_log_events(
            logGroupName=CLOUDWATCH_LOG_GROUP,
            logStreamName=stream["logStreamName"],
            startFromHead=False,
        )["events"]
        text += "\n".join(e["message"] for e in events)

    assert "training finished" in text.lower()