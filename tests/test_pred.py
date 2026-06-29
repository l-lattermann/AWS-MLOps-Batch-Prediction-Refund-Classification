import os
from dotenv import load_dotenv
from pathlib import Path
import time

import boto3
import requests

from storage.rds_connection import get_connection

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

API_URL = os.environ["API_URL"]
AWS_REGION = os.environ["AWS_REGION"]
ECS_CLUSTER_NAME = os.environ["ECS_CLUSTER_NAME"]
CLOUDWATCH_LOG_GROUP = os.environ.get("CLOUDWATCH_LOG_GROUP", "/ecs/refund-classification")

ecs = boto3.client("ecs", region_name=AWS_REGION)
logs = boto3.client("logs", region_name=AWS_REGION)


def test_prediction_pipeline():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM images WHERE status='PENDING'")
            pending_before = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM predictions")
            predictions_before = cur.fetchone()[0]

    assert pending_before > 0

    res = requests.post(f"{API_URL}/predictions/run", timeout=30)
    assert res.status_code == 200

    task_arn = res.json()["task_arn"]
    print("Started:", task_arn)

    timeout = time.time() + 60 * 30

    while True:
        task = ecs.describe_tasks(
            cluster=ECS_CLUSTER_NAME,
            tasks=[task_arn],
        )["tasks"][0]

        container = task["containers"][0]

        if container.get("lastStatus") == "STOPPED":
            assert container.get("exitCode", 1) == 0
            break

        assert time.time() < timeout
        time.sleep(10)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, images_processed, images_failed
                FROM prediction_runs
                ORDER BY started_at DESC
                LIMIT 1
            """)
            run = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM predictions")
            predictions_after = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM images WHERE status='PREDICTED'")
            predicted = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM images WHERE status='FAILED'")
            failed = cur.fetchone()[0]

    assert run[0] == "SUCCESS"
    assert run[2] == 0
    assert predictions_after > predictions_before
    assert predicted >= run[1]

    streams = logs.describe_log_streams(
        logGroupName=CLOUDWATCH_LOG_GROUP,
        orderBy="LastEventTime",
        descending=True,
        limit=1,
    )["logStreams"]

    assert streams

    events = logs.get_log_events(
        logGroupName=CLOUDWATCH_LOG_GROUP,
        logStreamName=streams[0]["logStreamName"],
        startFromHead=False,
    )["events"]

    text = "\n".join(e["message"] for e in events)

    assert "Batch prediction finished" in text
    assert failed == 0