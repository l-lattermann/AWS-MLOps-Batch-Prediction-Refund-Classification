import logging
import os
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

AWS_REGION = os.environ["AWS_REGION"]
S3_BUCKET = os.environ["S3_BUCKET"]
TRAIN_CONFIG_S3_KEY = os.environ["TRAIN_CONFIG_S3_KEY"]

MODELS_PREFIX = os.environ.get("MODELS_PREFIX", "models/").rstrip("/")
METADATA_PREFIX = os.environ.get("METADATA_PREFIX", "metadata/").rstrip("/")
CLOUDWATCH_LOG_GROUP = os.environ.get("CLOUDWATCH_LOG_GROUP")
CLOUDWATCH_LOG_STREAM = os.environ.get("CLOUDWATCH_LOG_STREAM")

s3 = boto3.client("s3", region_name=AWS_REGION)


def load_training_config() -> dict:
    response = s3.get_object(Bucket=S3_BUCKET, Key=TRAIN_CONFIG_S3_KEY)
    return yaml.safe_load(response["Body"].read().decode("utf-8"))


config = load_training_config()

DATASET_ID = config["dataset"]["id"]
DATASET_VERSION = config["dataset"]["version"]
MODEL_VERSION = config["model"]["version"]

DATASET_DIR = Path(config["dataset"]["local_path"])
TRAIN_DIR = DATASET_DIR / config["dataset"]["train_dir"]
VAL_DIR = DATASET_DIR / config["dataset"]["validation_dir"]
TEST_DIR = DATASET_DIR / config["dataset"]["test_dir"]

MODEL_PATH = Path(config["output"]["model_path"])
METADATA_PATH = Path(config["output"]["metadata_path"])

ARCHITECTURE = config["model"]["architecture"]
IMAGE_SIZE = config["model"]["image_size"]
PRETRAINED = config["model"]["pretrained"]

NUM_EPOCHS = config["training"]["epochs"]
BATCH_SIZE = config["training"]["batch_size"]
LEARNING_RATE = config["training"]["learning_rate"]

S3_MODEL_PATH = f"{MODELS_PREFIX}/{MODEL_VERSION}/{MODEL_PATH.name}"
S3_METADATA_PATH = f"{METADATA_PREFIX}/{MODEL_VERSION}/{METADATA_PATH.name}"


def upload_file(local_path: Path, key: str) -> None:
    logger.info("Uploading %s -> s3://%s/%s", local_path, S3_BUCKET, key)
    s3.upload_file(str(local_path), S3_BUCKET, key)


def create_training_run(conn, run_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO models (
                model_version, architecture, dataset_id, dataset_version,
                s3_model_path, s3_metadata_path, active
            )
            VALUES (%s, %s, %s, %s, %s, %s, FALSE)
            ON CONFLICT (model_version)
            DO UPDATE SET
                architecture = EXCLUDED.architecture,
                dataset_id = EXCLUDED.dataset_id,
                dataset_version = EXCLUDED.dataset_version,
                s3_model_path = EXCLUDED.s3_model_path,
                s3_metadata_path = EXCLUDED.s3_metadata_path,
                active = FALSE;
            """,
            (MODEL_VERSION, ARCHITECTURE, DATASET_ID, DATASET_VERSION, S3_MODEL_PATH, S3_METADATA_PATH),
        )
        cur.execute(
            """
            INSERT INTO training_runs (
                run_id, model_version, dataset_id, dataset_version, status,
                epochs, batch_size, learning_rate, cloudwatch_log_group, cloudwatch_log_stream
            )
            VALUES (%s, %s, %s, %s, 'RUNNING', %s, %s, %s, %s, %s);
            """,
            (
                run_id,
                MODEL_VERSION,
                DATASET_ID,
                DATASET_VERSION,
                NUM_EPOCHS,
                BATCH_SIZE,
                LEARNING_RATE,
                CLOUDWATCH_LOG_GROUP,
                CLOUDWATCH_LOG_STREAM,
            ),
        )
    conn.commit()


def mark_training_success(conn, run_id: str, train_loss: float, val_accuracy: float, test_accuracy: float, runtime: float) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE training_runs
            SET status = 'SUCCESS',
                train_loss = %s,
                validation_accuracy = %s,
                test_accuracy = %s,
                training_duration_seconds = %s,
                finished_at = CURRENT_TIMESTAMP
            WHERE run_id = %s;
            """,
            (train_loss, val_accuracy, test_accuracy, runtime, run_id),
        )
        cur.execute(
            """
            UPDATE models
            SET active = TRUE,
                s3_model_path = %s,
                s3_metadata_path = %s
            WHERE model_version = %s;
            """,
            (S3_MODEL_PATH, S3_METADATA_PATH, MODEL_VERSION),
        )
    conn.commit()


def mark_training_failed(conn, run_id: str, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE training_runs
            SET status = 'FAILED',
                error_message = %s,
                finished_at = CURRENT_TIMESTAMP
            WHERE run_id = %s;
            """,
            (error, run_id),
        )
    conn.commit()


def get_transform() -> transforms.Compose:
    return transforms.Compose([
        transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])


def get_dataloader(data_dir: Path, shuffle: bool = False) -> tuple[DataLoader, list[str]]:
    dataset = datasets.ImageFolder(data_dir, transform=get_transform())
    logger.info("Dataset loaded | path=%s images=%s classes=%s", data_dir, len(dataset), len(dataset.classes))
    return DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=shuffle), dataset.classes


def train_one_epoch(model: nn.Module, dataloader: DataLoader, criterion: nn.Module, optimizer: optim.Optimizer, device: str) -> float:
    model.train()
    total_loss = 0.0
    for images, labels in dataloader:
        images, labels = images.to(device), labels.to(device)
        optimizer.zero_grad()
        loss = criterion(model(images), labels)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
    return total_loss / len(dataloader)


def evaluate(model: nn.Module, dataloader: DataLoader, device: str) -> float:
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for images, labels in dataloader:
            images, labels = images.to(device), labels.to(device)
            predictions = model(images).argmax(dim=1)
            correct += (predictions == labels).sum().item()
            total += labels.size(0)
    return correct / total


def load_datasets() -> tuple[DataLoader, DataLoader, DataLoader, list[str]]:
    train_loader, class_names = get_dataloader(TRAIN_DIR, shuffle=True)
    val_loader, val_classes = get_dataloader(VAL_DIR)
    test_loader, test_classes = get_dataloader(TEST_DIR)
    if class_names != val_classes or class_names != test_classes:
        raise ValueError("Class names differ between train, validation and test sets.")
    return train_loader, val_loader, test_loader, class_names


def train_model(model: nn.Module, train_loader: DataLoader, val_loader: DataLoader, device: str) -> tuple[float, float]:
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)

    best_val_accuracy = 0.0
    best_train_loss = 0.0
    best_state_dict = deepcopy(model.state_dict())

    logger.info("Training started | epochs=%s batch_size=%s lr=%s", NUM_EPOCHS, BATCH_SIZE, LEARNING_RATE)

    for epoch in range(NUM_EPOCHS):
        t_epoch_start = perf_counter()
        train_loss = train_one_epoch(model, train_loader, criterion, optimizer, device)
        val_accuracy = evaluate(model, val_loader, device)

        if val_accuracy > best_val_accuracy:
            best_val_accuracy = val_accuracy
            best_train_loss = train_loss
            best_state_dict = deepcopy(model.state_dict())
            logger.info("New best model | epoch=%s val_accuracy=%.4f", epoch + 1, val_accuracy)

        logger.info(
            "Epoch %s/%s | loss=%.4f | val_accuracy=%.4f | epoch_time=%.2fs",
            epoch + 1,
            NUM_EPOCHS,
            train_loss,
            val_accuracy,
            perf_counter() - t_epoch_start,
        )

    model.load_state_dict(best_state_dict)
    return best_train_loss, best_val_accuracy


def main() -> None:
    run_id = str(uuid.uuid4())
    t_run_start = perf_counter()
    device = "cuda" if torch.cuda.is_available() else "cpu"

    with get_connection() as conn:
        create_training_run(conn, run_id)

        try:
            logger.info("Training job started | run_id=%s model_version=%s device=%s architecture=%s", run_id, MODEL_VERSION, device, ARCHITECTURE)

            train_loader, val_loader, test_loader, class_names = load_datasets()
            model = create_model(num_classes=len(class_names), architecture=ARCHITECTURE, pretrained=PRETRAINED).to(device)

            train_loss, val_accuracy = train_model(model, train_loader, val_loader, device)
            test_accuracy = evaluate(model, test_loader, device)
            runtime = perf_counter() - t_run_start

            save_model(model=model, path=MODEL_PATH, class_names=class_names, architecture=ARCHITECTURE, image_size=IMAGE_SIZE)

            metadata = build_training_metadata(
                model_version=MODEL_VERSION,
                architecture=ARCHITECTURE,
                dataset_id=DATASET_ID,
                dataset_version=DATASET_VERSION,
                train_loss=train_loss,
                val_accuracy=val_accuracy,
                test_accuracy=test_accuracy,
                epochs=NUM_EPOCHS,
                batch_size=BATCH_SIZE,
                learning_rate=LEARNING_RATE,
                image_size=IMAGE_SIZE,
                training_duration_seconds=runtime,
            )

            save_metadata_json(metadata, METADATA_PATH)
            upload_file(MODEL_PATH, S3_MODEL_PATH)
            upload_file(METADATA_PATH, S3_METADATA_PATH)
            mark_training_success(conn, run_id, train_loss, val_accuracy, test_accuracy, runtime)

            logger.info(
                "Training job finished | run_id=%s model=%s metadata=%s train_loss=%.4f val_accuracy=%.4f test_accuracy=%.4f runtime=%.2fs",
                run_id,
                S3_MODEL_PATH,
                S3_METADATA_PATH,
                train_loss,
                val_accuracy,
                test_accuracy,
                runtime,
            )

        except Exception as exc:
            logger.exception("Training job failed | run_id=%s", run_id)
            mark_training_failed(conn, run_id, str(exc))
            raise


if __name__ == "__main__":
    main()