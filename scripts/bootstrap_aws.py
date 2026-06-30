"""Upload project assets to AWS and register them in RDS."""

import hashlib
import json
from dotenv import load_dotenv
import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from app.logging_config import setup_logger
from storage.rds_connection import get_connection_from_secret

setup_logger(__name__)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

INFRASTRUCTURE_OUTPUT_PATH = ROOT / os.environ["INFRASTRUCTURE_OUTPUT_PATH"]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
MODEL_DIR = ROOT / "ml" / "models"
DEFAULT_DATASET_VERSION = "v1"


def load_infrastructure() -> dict:
    """Load Terraform outputs required for AWS bootstrap."""
    with INFRASTRUCTURE_OUTPUT_PATH.open(encoding="utf-8") as f:
        outputs = json.load(f)
    return {
        "bucket": outputs["s3_bucket"]["value"],
        "env": outputs["app_environment"]["value"],
    }


def stable_id(value: str) -> str:
    """Create a deterministic short ID from a string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def object_exists(s3, bucket: str, key: str) -> bool:
    """Check whether an object already exists in S3."""
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def upload_file(s3, bucket: str, local_path: Path, key: str) -> None:
    """Upload a file to S3 if it does not already exist."""
    if object_exists(s3, bucket, key):
        logger.info("Skipping existing object s3://%s/%s", bucket, key)
        return

    logger.info("Uploading %s -> s3://%s/%s", local_path, bucket, key)
    s3.upload_file(str(local_path), bucket, key)


def upload_directory(s3, bucket: str, local_dir: Path, s3_prefix: str) -> None:
    """Upload all files from a directory to an S3 prefix."""
    for path in local_dir.rglob("*"):
        if path.is_file():
            key = f"{s3_prefix}/{path.relative_to(local_dir).as_posix()}"
            upload_file(s3, bucket, path, key)


def upload_train_configs(s3, bucket: str, env: dict) -> list[dict[str, str]]:
    """Upload training configs and return their database records."""
    configs_prefix = env.get("CONFIGS_PREFIX", "configs/").rstrip("/")
    uploaded_configs = []

    train_dir = CONFIG_DIR / "train"

    for path in train_dir.glob("*.yml"):
        s3_key = f"{configs_prefix}/train/{path.name}"
        upload_file(s3, bucket, path, s3_key)

        uploaded_configs.append({
            "config_id": stable_id(s3_key),
            "config_type": "TRAIN",
            "s3_key": s3_key,
        })

    return uploaded_configs


def upload_batch_prediction_configs(s3, bucket: str, env: dict) -> list[dict[str, str]]:
    """Upload prediction configs and return their database records."""
    configs_prefix = env.get("CONFIGS_PREFIX", "configs/").rstrip("/")
    uploaded_configs = []

    batch_dir = CONFIG_DIR / "pred"

    for path in batch_dir.glob("*.yml"):
        s3_key = f"{configs_prefix}/pred/{path.name}"
        upload_file(s3, bucket, path, s3_key)

        uploaded_configs.append({
            "config_id": stable_id(s3_key),
            "config_type": "PREDICTION",
            "s3_key": s3_key,
        })

    return uploaded_configs


def normalize_dataset_id(name: str) -> str:
    """Convert a dataset folder name into a stable dataset ID."""
    return name.replace("-", "_")


def upload_datasets(s3, bucket: str, env: dict) -> list[dict[str, str]]:
    """Upload local datasets and return their database records."""
    datasets_prefix = env.get("DATASETS_PREFIX", "datasets/").rstrip("/")
    datasets = []

    for dataset_dir in DATA_DIR.iterdir():
        if not dataset_dir.is_dir():
            continue

        dataset_id = normalize_dataset_id(dataset_dir.name)
        dataset_version = DEFAULT_DATASET_VERSION
        s3_prefix = f"{datasets_prefix}/{dataset_id}/{dataset_version}"

        logger.info("Uploading dataset %s version %s -> s3://%s/%s", dataset_id, dataset_version, bucket, s3_prefix)
        upload_directory(s3, bucket, dataset_dir, s3_prefix)

        datasets.append({
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "name": dataset_dir.name,
            "s3_prefix": s3_prefix,
        })

    return datasets


def upload_models(s3, bucket: str, env: dict) -> list[dict[str, str]]:
    """Upload local model artifacts and return their database records."""
    models_prefix = env.get("MODELS_PREFIX", "models/").rstrip("/")
    uploaded_models = []

    for metadata_path in MODEL_DIR.glob("*_metadata.json"):
        with metadata_path.open(encoding="utf-8") as f:
            metadata = json.load(f)

        model_version = metadata["model_version"]
        model_path = MODEL_DIR / f"{model_version}.pth"

        if not model_path.exists():
            raise FileNotFoundError(f"Missing model file for metadata {metadata_path}: {model_path}")

        s3_model_path = f"{models_prefix}/{model_version}/{model_path.name}"
        s3_metadata_path = f"{models_prefix}/{model_version}/{metadata_path.name}"

        upload_file(s3, bucket, model_path, s3_model_path)
        upload_file(s3, bucket, metadata_path, s3_metadata_path)

        uploaded_models.append({
            "model_version": model_version,
            "architecture": metadata["architecture"],
            "dataset_id": metadata["dataset_id"],
            "dataset_version": metadata["dataset_version"],
            "s3_model_path": s3_model_path,
            "s3_metadata_path": s3_metadata_path,
            "active": True,
        })

    return uploaded_models


def upload_validation_as_incoming_images(s3, bucket: str, env: dict) -> list[dict[str, str]]:
    """Upload validation images as pending production-like inputs."""
    incoming_prefix = env.get("INCOMING_IMAGES_PREFIX", "incoming-images/").rstrip("/")
    incoming_images = []
    counter = 1

    for dataset_dir in DATA_DIR.iterdir():
        if not dataset_dir.is_dir():
            continue

        validation_dir = dataset_dir / "validation"
        if not validation_dir.exists():
            continue

        for path in sorted(validation_dir.rglob("*")):
            if not path.is_file():
                continue

            extension = path.suffix.lower()
            filename = f"incoming_{counter:05d}{extension}"
            s3_key = f"{incoming_prefix}/{filename}"

            upload_file(s3, bucket, path, s3_key)

            incoming_images.append({
                "image_id": stable_id(s3_key),
                "s3_key": s3_key,
                "filename": filename,
            })

            counter += 1

    return incoming_images


def upsert_config(conn, config_id: str, config_type: str, s3_key: str) -> None:
    """Create or update a config record."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO configs (config_id, config_type, s3_key)
            VALUES (%s, %s, %s)
            ON CONFLICT (s3_key)
            DO UPDATE SET
                config_type = EXCLUDED.config_type,
                created_at = CURRENT_TIMESTAMP;
            """,
            (config_id, config_type, s3_key),
        )


def upsert_dataset(conn, dataset_id: str, dataset_version: str, name: str, s3_prefix: str) -> None:
    """Create or update a dataset record."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO datasets (dataset_id, dataset_version, name, s3_prefix)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (dataset_id, dataset_version)
            DO UPDATE SET
                name = EXCLUDED.name,
                s3_prefix = EXCLUDED.s3_prefix;
            """,
            (dataset_id, dataset_version, name, s3_prefix),
        )


def upsert_model(
    conn,
    model_version: str,
    architecture: str,
    dataset_id: str,
    dataset_version: str,
    s3_model_path: str,
    s3_metadata_path: str,
    active: bool,
) -> None:
    """Create or update a model record."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO models (
                model_version,
                architecture,
                dataset_id,
                dataset_version,
                s3_model_path,
                s3_metadata_path,
                active
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (model_version)
            DO UPDATE SET
                architecture = EXCLUDED.architecture,
                dataset_id = EXCLUDED.dataset_id,
                dataset_version = EXCLUDED.dataset_version,
                s3_model_path = EXCLUDED.s3_model_path,
                s3_metadata_path = EXCLUDED.s3_metadata_path,
                active = EXCLUDED.active;
            """,
            (
                model_version,
                architecture,
                dataset_id,
                dataset_version,
                s3_model_path,
                s3_metadata_path,
                active,
            ),
        )


def upsert_image(conn, image_id: str, s3_key: str, filename: str) -> None:
    """Create or reset a pending image record."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO images (image_id, s3_key, filename, status)
            VALUES (%s, %s, %s, 'PENDING')
            ON CONFLICT (s3_key)
            DO UPDATE SET
                filename = EXCLUDED.filename,
                status = 'PENDING';
            """,
            (image_id, s3_key, filename),
        )


def main() -> None:
    """Upload bootstrap assets and upsert their RDS records."""
    infra = load_infrastructure()
    bucket = infra["bucket"]
    env = infra["env"]

    s3 = boto3.client("s3", region_name=env["AWS_REGION"])

    uploaded_train_configs = upload_train_configs(s3, bucket, env)
    uploaded_prediction_configs = upload_batch_prediction_configs(s3, bucket, env)
    uploaded_datasets = upload_datasets(s3, bucket, env)
    uploaded_models = upload_models(s3, bucket, env)
    uploaded_incoming_images = upload_validation_as_incoming_images(s3, bucket, env)

    with get_connection_from_secret(
        secret_arn=env["POSTGRES_SECRET_ARN"],
        region=env["AWS_REGION"],
        host=env["POSTGRES_HOST"],
        port=int(env["POSTGRES_PORT"]),
        dbname=env["POSTGRES_DB_NAME"],
    ) as conn:
        for config in uploaded_train_configs:
            upsert_config(conn, **config)

        for config in uploaded_prediction_configs:
            upsert_config(conn, **config)

        for dataset in uploaded_datasets:
            upsert_dataset(conn, **dataset)

        for model in uploaded_models:
            upsert_model(conn, **model)

        for image in uploaded_incoming_images:
            upsert_image(conn, **image)

        conn.commit()

    logger.info("AWS bootstrap finished.")

if __name__ == "__main__":
    main()