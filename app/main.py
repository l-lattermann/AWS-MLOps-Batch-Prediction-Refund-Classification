"""
FastAPI application.

Responsibilities:
- REST API endpoints
- Upload images (path to local dir)
- Manual predictions (use latest pred config)
- Manual training start (use latest trrain config)
- Health checks (print train config, print pred config, model, metadata,  other health checks?)
"""


import os
import uuid
from pathlib import Path

import boto3
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from storage.rds_connection import get_connection

load_dotenv(override=True)

AWS_REGION = os.environ["AWS_REGION"]
S3_BUCKET = os.environ["S3_BUCKET"]
INCOMING_IMAGES_PREFIX = os.environ.get("INCOMING_IMAGES_PREFIX", "incoming-images/").rstrip("/")

ECS_CLUSTER_NAME = os.environ.get("ECS_CLUSTER_NAME")
TRAIN_TASK_DEFINITION = os.environ.get("TRAIN_TASK_DEFINITION")
BATCH_TASK_DEFINITION = os.environ.get("BATCH_TASK_DEFINITION")
ECS_SUBNET_IDS = os.environ.get("ECS_SUBNET_IDS", "")
ECS_SECURITY_GROUP_IDS = os.environ.get("ECS_SECURITY_GROUP_IDS", "")

s3 = boto3.client("s3", region_name=AWS_REGION)
ecs = boto3.client("ecs", region_name=AWS_REGION)

app = FastAPI(title="Refund Classification API")


class UploadDirRequest(BaseModel):
    directory: str


def s3_yaml(key: str) -> dict:
    res = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return yaml.safe_load(res["Body"].read().decode("utf-8"))


def latest_config(config_type: str) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s3_key
                FROM configs
                WHERE config_type = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (config_type,),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"No {config_type} config found")

    return {"s3_key": row[0], "config": s3_yaml(row[0])}


def active_model() -> dict | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT model_version, architecture, s3_model_path, s3_metadata_path, created_at
                FROM models
                WHERE active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()

    if row is None:
        return None

    return {
        "model_version": row[0],
        "architecture": row[1],
        "s3_model_path": row[2],
        "s3_metadata_path": row[3],
        "created_at": str(row[4]),
    }


def run_ecs_task(task_definition: str | None, container_name: str, command: list[str]) -> dict:
    if not ECS_CLUSTER_NAME or not task_definition or not ECS_SUBNET_IDS or not ECS_SECURITY_GROUP_IDS:
        raise HTTPException(status_code=500, detail="Missing ECS env vars")

    res = ecs.run_task(
        cluster=ECS_CLUSTER_NAME,
        launchType="FARGATE",
        taskDefinition=task_definition,
        enableExecuteCommand=True,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": [s for s in ECS_SUBNET_IDS.split(",") if s],
                "securityGroups": [s for s in ECS_SECURITY_GROUP_IDS.split(",") if s],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [{
                "name": container_name,
                "command": command,
            }]
        },
    )

    if res.get("failures"):
        raise HTTPException(status_code=500, detail=res["failures"])

    return {"started": True, "task_arn": res["tasks"][0]["taskArn"]}


@app.get("/")
def root():
    return {"service": "refund-classification", "status": "running"}


@app.get("/health")
def health():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            db_ok = cur.fetchone()[0] == 1

    s3.head_bucket(Bucket=S3_BUCKET)

    return {
        "status": "ok",
        "database": db_ok,
        "bucket": S3_BUCKET,
        "train_config": latest_config("TRAIN")["s3_key"],
        "prediction_config": latest_config("PREDICTION")["s3_key"],
        "active_model": active_model(),
    }


@app.get("/model/active")
def model_active():
    model = active_model()
    if model is None:
        raise HTTPException(status_code=404, detail="No active model found")
    return model


@app.post("/images/upload-dir")
def upload_images(req: UploadDirRequest):
    directory = Path(req.directory)

    if not directory.is_dir():
        raise HTTPException(status_code=400, detail=f"Invalid directory: {directory}")

    uploaded = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            for path in sorted(directory.rglob("*")):
                if not path.is_file():
                    continue

                image_id = str(uuid.uuid4())
                s3_key = f"{INCOMING_IMAGES_PREFIX}/{image_id}_{path.name}"

                s3.upload_file(str(path), S3_BUCKET, s3_key)

                cur.execute(
                    """
                    INSERT INTO images (image_id, s3_key, filename, status)
                    VALUES (%s, %s, %s, 'PENDING')
                    """,
                    (image_id, s3_key, path.name),
                )

                uploaded.append({"image_id": image_id, "s3_key": s3_key, "filename": path.name})

        conn.commit()

    return {"uploaded": len(uploaded), "images": uploaded}


@app.post("/training/run")
def run_training():
    return run_ecs_task(TRAIN_TASK_DEFINITION, "train", ["python", "-m", "ml.train"])


@app.post("/predictions/run")
def run_prediction():
    return run_ecs_task(BATCH_TASK_DEFINITION, "batch", ["python", "-m", "ml.batch_predict"])