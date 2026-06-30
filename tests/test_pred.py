"""Integration test verifying that batch prediction runs and stores monitoring data."""

import os
from pathlib import Path
import time

import boto3
import requests
from dotenv import load_dotenv

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
    """Verify that batch prediction completes and writes prediction metadata."""
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

    timeout = time.time() + 60 * 30

    while True:
        task = ecs.describe_tasks(cluster=ECS_CLUSTER_NAME, tasks=[task_arn])["tasks"][0]
        container = task["containers"][0]

        if container.get("lastStatus") == "STOPPED":
            assert container.get("exitCode", 1) == 0
            break

        assert time.time() < timeout
        time.sleep(10)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT run_id, model_version, status, images_processed, images_failed,
                       confidence_mean, confidence_min, confidence_max,
                       confidence_p05, confidence_p50, confidence_p95,
                       low_confidence_count, low_confidence_rate,
                       cloudwatch_log_group, cloudwatch_log_stream,
                       error_message, finished_at
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

            cur.execute("""
                SELECT COUNT(*), COUNT(DISTINCT predicted_class), SUM(prediction_count), SUM(prediction_share)
                FROM prediction_class_stats
                WHERE run_id = %s
            """, (run[0],))
            class_stats = cur.fetchone()

            cur.execute("""
                SELECT COUNT(*), MIN(confidence), MAX(confidence), AVG(confidence)
                FROM predictions
                WHERE run_id = %s
            """, (run[0],))
            pred_stats = cur.fetchone()

    assert run is not None

    run_id = run[0]
    status = run[2]
    images_processed = run[3]
    images_failed = run[4]
    confidence_mean = run[5]
    confidence_min = run[6]
    confidence_max = run[7]
    confidence_p05 = run[8]
    confidence_p50 = run[9]
    confidence_p95 = run[10]
    low_confidence_count = run[11]
    low_confidence_rate = run[12]
    cloudwatch_log_group = run[13]
    cloudwatch_log_stream = run[14]
    error_message = run[15]
    finished_at = run[16]

    assert run_id
    assert status == "SUCCESS"
    assert images_processed > 0
    assert images_failed == 0
    assert error_message is None
    assert finished_at is not None

    assert predictions_after > predictions_before
    assert pred_stats[0] == images_processed
    assert predicted >= images_processed
    assert failed == 0

    assert confidence_mean is not None
    assert confidence_min is not None
    assert confidence_max is not None
    assert confidence_p05 is not None
    assert confidence_p50 is not None
    assert confidence_p95 is not None

    # Stored confidence statistics must be valid probabilities.
    assert 0 <= confidence_min <= confidence_mean <= confidence_max <= 1
    assert 0 <= confidence_p05 <= confidence_p50 <= confidence_p95 <= 1
    assert low_confidence_count >= 0
    assert 0 <= low_confidence_rate <= 1

    # Class distribution must cover all processed images.
    assert class_stats[0] > 0
    assert class_stats[1] > 0
    assert class_stats[2] == images_processed
    assert abs(float(class_stats[3]) - 1.0) < 0.0001

    assert cloudwatch_log_group == CLOUDWATCH_LOG_GROUP
    assert cloudwatch_log_stream is not None

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

    assert "Batch prediction finished" in text