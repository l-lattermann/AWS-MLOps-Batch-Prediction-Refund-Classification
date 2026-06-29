import logging
import os
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path
from time import perf_counter

import boto3
import torch
import yaml
from torch import nn, optim
from torch.utils.data import DataLoader
from torchvision import datasets, transforms

from app.logging_config import setup_logger
from ml.metadata import build_training_metadata, save_metadata_json
from ml.model import create_model, save_model
from storage.rds_connection import get_connection

setup_logger(__name__)
logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
MODELS_PREFIX = os.environ.get("MODELS_PREFIX", "").rstrip("/")
CLOUDWATCH_LOG_GROUP = os.environ.get("CLOUDWATCH_LOG_GROUP", "")
CLOUDWATCH_LOG_STREAM = os.environ.get("CLOUDWATCH_LOG_STREAM", "")

s3 = boto3.client("s3", region_name=AWS_REGION)


def latest_config_key(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s3_key
            FROM configs
            WHERE config_type = 'TRAIN'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("No TRAIN config found in RDS.")
    return row[0]


def load_yaml_from_s3(key: str) -> dict:
    response = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return yaml.safe_load(response["Body"].read().decode("utf-8"))


def dataset_prefix(conn, dataset_id: str, dataset_version: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s3_prefix
            FROM datasets
            WHERE dataset_id = %s AND dataset_version = %s
            """,
            (dataset_id, dataset_version),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"Dataset not found in RDS: {dataset_id}:{dataset_version}")
    return row[0].rstrip("/")


def download_prefix(prefix: str, target_dir: Path) -> None:
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{prefix}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel = Path(key).relative_to(prefix)
            target = target_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Downloading s3://%s/%s -> %s", S3_BUCKET, key, target)
            s3.download_file(S3_BUCKET, key, str(target))


def upload_file(path: Path, key: str) -> None:
    logger.info("Uploading %s -> s3://%s/%s", path, S3_BUCKET, key)
    s3.upload_file(str(path), S3_BUCKET, key)


def transform(image_size: int) -> transforms.Compose:
    return transforms.Compose([
        transforms.Resize((image_size, image_size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])


def dataloader(path: Path, image_size: int, batch_size: int, shuffle: bool = False) -> tuple[DataLoader, list[str]]:
    ds = datasets.ImageFolder(path, transform=transform(image_size))
    logger.info("Dataset loaded | path=%s images=%s classes=%s", path, len(ds), len(ds.classes))
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle), ds.classes


def load_datasets(root: Path, cfg: dict) -> tuple[DataLoader, DataLoader, DataLoader, list[str]]:
    image_size = cfg["model"]["image_size"]
    batch_size = cfg["training"]["batch_size"]
    train_loader, classes = dataloader(root / cfg["dataset"]["train_dir"], image_size, batch_size, True)
    val_loader, val_classes = dataloader(root / cfg["dataset"]["validation_dir"], image_size, batch_size)
    test_loader, test_classes = dataloader(root / cfg["dataset"]["test_dir"], image_size, batch_size)
    if classes != val_classes or classes != test_classes:
        raise ValueError("Class names differ between train, validation and test sets.")
    return train_loader, val_loader, test_loader, classes


def train_one_epoch(model: nn.Module, loader: DataLoader, criterion: nn.Module, optimizer: optim.Optimizer, device: str) -> float:
    model.train()
    total = 0.0
    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)
        optimizer.zero_grad()
        loss = criterion(model(images), labels)
        loss.backward()
        optimizer.step()
        total += loss.item()
    return total / len(loader)


def evaluate(model: nn.Module, loader: DataLoader, device: str) -> float:
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for images, labels in loader:
            images, labels = images.to(device), labels.to(device)
            pred = model(images).argmax(dim=1)
            correct += (pred == labels).sum().item()
            total += labels.size(0)
    return correct / total


def train_model(model: nn.Module, train_loader: DataLoader, val_loader: DataLoader, cfg: dict, device: str) -> tuple[float, float]:
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=cfg["training"]["learning_rate"])
    best_loss, best_acc, best_state = 0.0, 0.0, deepcopy(model.state_dict())

    for epoch in range(cfg["training"]["epochs"]):
        start = perf_counter()
        loss = train_one_epoch(model, train_loader, criterion, optimizer, device)
        acc = evaluate(model, val_loader, device)
        if acc > best_acc:
            best_loss, best_acc, best_state = loss, acc, deepcopy(model.state_dict())
        logger.info("Epoch %s/%s | loss=%.4f | val_accuracy=%.4f | time=%.2fs", epoch + 1, cfg["training"]["epochs"], loss, acc, perf_counter() - start)

    model.load_state_dict(best_state)
    return best_loss, best_acc


def upsert_model(conn, cfg: dict, s3_model_path: str, s3_metadata_path: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO models (model_version, architecture, dataset_id, dataset_version, s3_model_path, s3_metadata_path, active)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (model_version)
            DO UPDATE SET
                architecture = EXCLUDED.architecture,
                dataset_id = EXCLUDED.dataset_id,
                dataset_version = EXCLUDED.dataset_version,
                s3_model_path = EXCLUDED.s3_model_path,
                s3_metadata_path = EXCLUDED.s3_metadata_path,
                active = TRUE
            """,
            (
                cfg["model"]["version"],
                cfg["model"]["architecture"],
                cfg["dataset"]["id"],
                cfg["dataset"]["version"],
                s3_model_path,
                s3_metadata_path,
            ),
        )


def insert_training_run(conn, run_id: str, cfg: dict, train_loss: float, val_acc: float, test_acc: float, runtime: float) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO training_runs (
                run_id, model_version, dataset_id, dataset_version, status,
                train_loss, validation_accuracy, test_accuracy,
                epochs, batch_size, learning_rate,
                training_duration_seconds, cloudwatch_log_group, cloudwatch_log_stream,
                finished_at
            )
            VALUES (%s, %s, %s, %s, 'SUCCESS', %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            (
                run_id,
                cfg["model"]["version"],
                cfg["dataset"]["id"],
                cfg["dataset"]["version"],
                train_loss,
                val_acc,
                test_acc,
                cfg["training"]["epochs"],
                cfg["training"]["batch_size"],
                cfg["training"]["learning_rate"],
                runtime,
                CLOUDWATCH_LOG_GROUP,
                CLOUDWATCH_LOG_STREAM,
            ),
        )


def main() -> None:
    run_id = str(uuid.uuid4())
    started = perf_counter()
    device = "cuda" if torch.cuda.is_available() else "cpu"

    with get_connection() as conn, tempfile.TemporaryDirectory() as tmp:
        cfg = load_yaml_from_s3(latest_config_key(conn))
        prefix = dataset_prefix(conn, cfg["dataset"]["id"], cfg["dataset"]["version"])
        dataset_dir = Path(tmp) / "dataset"
        output_dir = Path(tmp) / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        download_prefix(prefix, dataset_dir)
        train_loader, val_loader, test_loader, classes = load_datasets(dataset_dir, cfg)

        model = create_model(
            num_classes=len(classes),
            architecture=cfg["model"]["architecture"],
            pretrained=cfg["model"]["pretrained"],
        ).to(device)

        train_loss, val_acc = train_model(model, train_loader, val_loader, cfg, device)
        test_acc = evaluate(model, test_loader, device)
        runtime = perf_counter() - started

        model_path = output_dir / f"{cfg['model']['version']}.pth"
        metadata_path = output_dir / f"{cfg['model']['version']}_metadata.json"
        s3_model_path = f"{MODELS_PREFIX}/{cfg['model']['version']}/{model_path.name}"
        s3_metadata_path = f"{MODELS_PREFIX}/{cfg['model']['version']}/{metadata_path.name}"

        save_model(model=model, path=model_path, class_names=classes, architecture=cfg["model"]["architecture"], image_size=cfg["model"]["image_size"])

        metadata = build_training_metadata(
            model_version=cfg["model"]["version"],
            architecture=cfg["model"]["architecture"],
            dataset_id=cfg["dataset"]["id"],
            dataset_version=cfg["dataset"]["version"],
            train_loss=train_loss,
            val_accuracy=val_acc,
            test_accuracy=test_acc,
            epochs=cfg["training"]["epochs"],
            batch_size=cfg["training"]["batch_size"],
            learning_rate=cfg["training"]["learning_rate"],
            image_size=cfg["model"]["image_size"],
            training_duration_seconds=runtime,
        )
        save_metadata_json(metadata, metadata_path)

        upload_file(model_path, s3_model_path)
        upload_file(metadata_path, s3_metadata_path)

        upsert_model(conn, cfg, s3_model_path, s3_metadata_path)
        insert_training_run(conn, run_id, cfg, train_loss, val_acc, test_acc, runtime)
        conn.commit()

    logger.info("Training finished | run_id=%s model=%s metadata=%s", run_id, s3_model_path, s3_metadata_path)


if __name__ == "__main__":
    main()