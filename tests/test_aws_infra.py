import os
from dotenv import load_dotenv
from pathlib import Path
import requests

import boto3
import pytest

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
    res = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix, MaxKeys=1)
    return "Contents" in res


def test_s3_bucket_online():
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.head_bucket(Bucket=S3_BUCKET)


@pytest.mark.parametrize("prefix", PREFIXES)
def test_s3_prefix_has_objects(prefix):
    s3 = boto3.client("s3", region_name=AWS_REGION)
    assert s3_has_objects(s3, prefix), f"No objects found under s3://{S3_BUCKET}/{prefix}"


def test_rds_online():
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
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            assert cur.fetchone()[0] > 0, f"{table} is empty"


def test_latest_train_config_exists():
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


def test_latest_prediction_config_exists():
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


def test_active_model_exists():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*)
                FROM models
                WHERE active = TRUE
            """)
            assert cur.fetchone()[0] == 1


def test_ecs_cluster_exists():
    ecs = boto3.client("ecs", region_name=AWS_REGION)
    res = ecs.describe_clusters(clusters=[ECS_CLUSTER_NAME])
    assert res["clusters"]
    assert res["clusters"][0]["status"] == "ACTIVE"


@pytest.mark.parametrize("task_definition", [
    TRAIN_TASK_DEFINITION,
    BATCH_TASK_DEFINITION,
])
def test_ecs_task_definition_active(task_definition):
    ecs = boto3.client("ecs", region_name=AWS_REGION)
    res = ecs.describe_task_definition(taskDefinition=task_definition)
    assert res["taskDefinition"]["taskDefinitionArn"] == task_definition
    assert res["taskDefinition"]["status"] == "ACTIVE"


def test_api_url_available():
    assert API_URL
    res = requests.get(API_URL, timeout=10)
    assert res.status_code == 200
    assert res.json()["status"] == "running"


def test_api_deep_health():
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
    res = requests.get(f"{API_URL}/model/active", timeout=10)
    assert res.status_code == 200

    body = res.json()
    assert body["model_version"]
    assert body["architecture"]
    assert body["s3_model_path"]


def test_api_service_running():
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