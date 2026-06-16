import logging
from copy import deepcopy
import os
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
from storage.aws_secrets import get_secret


setup_logger(__name__)
logger = logging.getLogger(__name__)


def load_app_config() -> dict:
    return get_secret(
        secret_name=os.environ["APP_CONFIG_SECRET_NAME"],
        region=os.environ["AWS_REGION"],
    )


def load_training_config(app_config: dict) -> dict:
    client = boto3.client("s3", region_name=app_config["aws_region"])
    response = client.get_object(
        Bucket=app_config["s3_bucket"],
        Key=app_config["train_config_s3_key"],
    )

    return yaml.safe_load(response["Body"].read().decode("utf-8"))


app_config = load_app_config()
config = load_training_config(app_config)

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


def train_one_epoch(
    model: nn.Module,
    dataloader: DataLoader,
    criterion: nn.Module,
    optimizer: optim.Optimizer,
    device: str,
) -> float:
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


def train_model(
    model: nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    device: str,
) -> tuple[float, float]:
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
    t_run_start = perf_counter()
    device = "cuda" if torch.cuda.is_available() else "cpu"

    logger.info("Training job started | model_version=%s device=%s architecture=%s", MODEL_VERSION, device, ARCHITECTURE)

    train_loader, val_loader, test_loader, class_names = load_datasets()

    model = create_model(
        num_classes=len(class_names),
        architecture=ARCHITECTURE,
        pretrained=True,
    ).to(device)

    train_loss, val_accuracy = train_model(model, train_loader, val_loader, device)
    test_accuracy = evaluate(model, test_loader, device)

    runtime = perf_counter() - t_run_start

    save_model(
        model=model,
        path=MODEL_PATH,
        class_names=class_names,
        architecture=ARCHITECTURE,
        image_size=IMAGE_SIZE,
    )

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

    logger.info(
        "Training job finished | model=%s metadata=%s train_loss=%.4f val_accuracy=%.4f test_accuracy=%.4f runtime=%.2fs",
        MODEL_PATH,
        METADATA_PATH,
        train_loss,
        val_accuracy,
        test_accuracy,
        runtime,
    )


if __name__ == "__main__":
    main()