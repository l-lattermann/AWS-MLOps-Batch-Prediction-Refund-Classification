"""Integration tests verifying that deployed AWS resources and stored references are valid."""

import os
from pathlib import Path

import boto3
import pytest
import requests
from dotenv import load_dotenv

from storage.rds_connection import get_connection

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

AWS_REGION = os.environ["AWS_REGION"]
S3_BUCKET = os.environ["S3_BUCKET"]
ECS_CLUSTER_NAME = os.environ["ECS_CLUSTER_NAME"]
TRAIN_TASK_DEFINITION = os.environ["TRAIN_TASK_DEFINITION"]
BATCH_TASK_DEFINITION = os.environ["BATCH_TASK_DEFINITION"]
API_URL = os.environ["API_URL"]
API_SERVICE_NAME = os.environ["API_SERVICE_NAME"]

PREFIXES = [
    os.environ.get("CONFIGS_PREFIX", "configs/"),
    os.environ.get("DATASETS_PREFIX", "datasets/"),
    os.environ.get("MODELS_PREFIX", "models/"),
    os.environ.get("INCOMING_IMAGES_PREFIX", "incoming-images/"),
]


def s3_has_objects(s3, prefix: str) -> bool:
    """Return whether an S3 prefix contains at least one object."""
    res = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix, MaxKeys=1)
    return "Contents" in res


def test_s3_bucket_online():
    """Verify that the S3 bucket is reachable."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.head_bucket(Bucket=S3_BUCKET)


@pytest.mark.parametrize("prefix", PREFIXES)
def test_s3_prefix_has_objects(prefix):
    """Verify that required S3 prefixes contain uploaded objects."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    assert s3_has_objects(s3, prefix), f"No objects found under s3://{S3_BUCKET}/{prefix}"


def test_rds_online():
    """Verify that the PostgreSQL database is reachable."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1


@pytest.mark.parametrize("table", [
    "configs",
    "datasets",
    "models",
    "images",
])
def test_required_rds_tables_have_data(table):
    """Verify that required bootstrap tables contain data."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            assert cur.fetchone()[0] > 0, f"{table} is empty"


def test_latest_train_config_exists_in_rds_and_s3():
    """Verify that the latest training config exists in RDS and S3."""
    s3 = boto3.client("s3", region_name=AWS_REGION)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s3_key
                FROM configs
                WHERE config_type = 'TRAIN'
                ORDER BY created_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()

    assert row is not None
    assert row[0]

    s3.head_object(Bucket=S3_BUCKET, Key=row[0])


def test_latest_prediction_config_exists_in_rds_and_s3():
    """Verify that the latest prediction config exists in RDS and S3."""
    s3 = boto3.client("s3", region_name=AWS_REGION)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s3_key
                FROM configs
                WHERE config_type = 'PREDICTION'
                ORDER BY created_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()

    assert row is not None
    assert row[0]

    s3.head_object(Bucket=S3_BUCKET, Key=row[0])


def test_datasets_exist_in_rds_and_s3():
    """Verify that dataset records point to populated S3 prefixes."""
    s3 = boto3.client("s3", region_name=AWS_REGION)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT dataset_id, dataset_version, s3_prefix FROM datasets")
            rows = cur.fetchall()

    assert rows

    for dataset_id, dataset_version, s3_prefix in rows:
        assert dataset_id
        assert dataset_version
        assert s3_prefix
        assert s3_has_objects(s3, s3_prefix), f"No objects found under s3://{S3_BUCKET}/{s3_prefix}"


def test_active_model_exists_in_rds_and_s3():
    """Verify that exactly one active model exists and its artifacts exist in S3."""
    s3 = boto3.client("s3", region_name=AWS_REGION)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_version, architecture, s3_model_path, s3_metadata_path
                FROM models
                WHERE active = TRUE
            """)
            rows = cur.fetchall()

    assert len(rows) == 1

    model_version, architecture, model_path, metadata_path = rows[0]

    assert model_version
    assert architecture
    assert model_path
    assert metadata_path

    s3.head_object(Bucket=S3_BUCKET, Key=model_path)
    s3.head_object(Bucket=S3_BUCKET, Key=metadata_path)


def test_incoming_images_exist_in_rds_and_s3():
    """Verify that registered images point to existing S3 objects."""
    s3 = boto3.client("s3", region_name=AWS_REGION)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT image_id, s3_key, filename
                FROM images
                LIMIT 20
            """)
            rows = cur.fetchall()

    assert rows

    for image_id, s3_key, filename in rows:
        assert image_id
        assert s3_key
        assert filename

        s3.head_object(Bucket=S3_BUCKET, Key=s3_key)


def test_ecs_cluster_exists():
    """Verify that the ECS cluster is active."""
    ecs = boto3.client("ecs", region_name=AWS_REGION)
    res = ecs.describe_clusters(clusters=[ECS_CLUSTER_NAME])

    assert res["clusters"]
    assert res["clusters"][0]["status"] == "ACTIVE"


@pytest.mark.parametrize("task_definition", [
    TRAIN_TASK_DEFINITION,
    BATCH_TASK_DEFINITION,
])
def test_ecs_task_definition_active(task_definition):
    """Verify that required ECS task definitions are active."""
    ecs = boto3.client("ecs", region_name=AWS_REGION)
    res = ecs.describe_task_definition(taskDefinition=task_definition)

    assert res["taskDefinition"]["taskDefinitionArn"] == task_definition
    assert res["taskDefinition"]["status"] == "ACTIVE"


def test_api_url_available():
    """Verify that the API root endpoint is responding."""
    assert API_URL

    res = requests.get(API_URL, timeout=10)

    assert res.status_code == 200
    assert res.json()["status"] == "running"


def test_api_deep_health():
    """Verify that the API health endpoint reports healthy dependencies."""
    assert API_URL

    res = requests.get(f"{API_URL}/health", timeout=30)

    assert res.status_code == 200

    body = res.json()

    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["bucket"] == S3_BUCKET
    assert body["train_config"]
    assert body["prediction_config"]


def test_api_active_model():
    """Verify that the API active model endpoint returns model metadata."""
    res = requests.get(f"{API_URL}/model/active", timeout=10)

    assert res.status_code == 200

    body = res.json()

    assert body["model_version"]
    assert body["architecture"]
    assert body["s3_model_path"]


def test_api_service_running():
    """Verify that the API ECS service is running."""
    ecs = boto3.client("ecs", region_name=AWS_REGION)

    res = ecs.describe_services(
        cluster=ECS_CLUSTER_NAME,
        services=[API_SERVICE_NAME],
    )

    assert not res.get("failures")
    assert res["services"]

    service = res["services"][0]

    assert service["status"] == "ACTIVE"
    assert service["desiredCount"] == 1
    assert service["runningCount"] == 1