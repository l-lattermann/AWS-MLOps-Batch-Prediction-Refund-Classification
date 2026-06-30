from pathlib import Path
import subprocess
from datetime import datetime, timezone
import json


def get_git_commit_hash() -> str | None:
    """Return the current Git commit hash, or None if unavailable."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
        ).strip()
    except Exception:
        return None


def build_training_metadata(
    model_version: str,
    architecture: str,
    dataset_id: str,
    dataset_version: str,
    train_loss: float,
    val_loss: float,
    train_accuracy: float,
    val_accuracy: float,
    test_accuracy: float,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    image_size: int,
    training_duration_seconds: float,
) -> dict:
    """Create the metadata dictionary stored alongside a trained model."""
    return {
        "model_version": model_version,
        "architecture": architecture,
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "train_loss": train_loss,
        "validation_loss": val_loss,
        "train_accuracy": train_accuracy,
        "validation_accuracy": val_accuracy,
        "test_accuracy": test_accuracy,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "image_size": image_size,
        "training_duration_seconds": training_duration_seconds,
        "git_commit_hash": get_git_commit_hash(),
    }


def save_metadata_json(
    metadata: dict,
    path: str | Path,
) -> None:
    """Save model metadata as a formatted JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            metadata,
            file,
            indent=4,
            ensure_ascii=False,
        )