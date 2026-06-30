"""Run batch predictions for pending images and store monitoring statistics."""

import logging
import os
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from time import perf_counter

import boto3
import torch
import yaml
from PIL import Image
from torchvision import transforms

from app.logging_config import setup_logger
from ml.model import create_model
from storage.rds_connection import get_connection

setup_logger(__name__)
logger = logging.getLogger(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
CLOUDWATCH_LOG_GROUP = os.environ.get("CLOUDWATCH_LOG_GROUP", "")
CLOUDWATCH_LOG_STREAM = os.environ.get("CLOUDWATCH_LOG_STREAM", "")
LOW_CONFIDENCE_THRESHOLD = float(os.environ.get("LOW_CONFIDENCE_THRESHOLD", "0.60"))

s3 = boto3.client("s3", region_name=AWS_REGION)


def percentile(values: list[float], p: float) -> float | None:
    """Calculate a percentile using linear interpolation."""
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * p
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] if f == c else xs[f] * (c - k) + xs[c] * (k - f)


def latest_prediction_config_key(conn) -> str:
    """Return the latest prediction config S3 key."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s3_key
            FROM configs
            WHERE config_type = 'PREDICTION'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("No PREDICTION config found in RDS.")
    return row[0]


def load_yaml_from_s3(key: str) -> dict:
    """Load and parse a YAML file from S3."""
    res = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return yaml.safe_load(res["Body"].read().decode("utf-8"))


def active_model(conn) -> dict:
    """Return the active model metadata from RDS."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT model_version, s3_model_path
            FROM models
            WHERE active = TRUE
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("No active model found in RDS.")
    return {"model_version": row[0], "s3_model_path": row[1]}


def pending_images(conn, limit: int) -> list[dict]:
    """Return pending images in upload order."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT image_id, s3_key, filename
            FROM images
            WHERE status = 'PENDING'
            ORDER BY uploaded_at ASC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    return [{"image_id": r[0], "s3_key": r[1], "filename": r[2]} for r in rows]


def load_model(model_info: dict, device: str, tmp: Path):
    """Download and initialize the active PyTorch model."""
    model_path = tmp / Path(model_info["s3_model_path"]).name
    s3.download_file(S3_BUCKET, model_info["s3_model_path"], str(model_path))
    ckpt = torch.load(model_path, map_location=device, weights_only=False)
    model = create_model(num_classes=ckpt["num_classes"], architecture=ckpt["architecture"], pretrained=False)
    model.load_state_dict(ckpt["model_state_dict"])
    model.to(device).eval()
    return model, ckpt["class_names"], ckpt["image_size"]


def image_transform(image_size: int) -> transforms.Compose:
    """Create the image preprocessing pipeline used during inference."""
    return transforms.Compose([
        transforms.Resize((image_size, image_size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])


def predict(model, image_path: Path, class_names: list[str], image_size: int, device: str) -> tuple[str, float]:
    """Predict the most likely class and confidence for one image."""
    image = Image.open(image_path).convert("RGB")
    tensor = image_transform(image_size)(image).unsqueeze(0).to(device)
    with torch.no_grad():
        probs = torch.softmax(model(tensor), dim=1)[0]
        conf, idx = torch.max(probs, dim=0)
    return class_names[int(idx.item())], float(conf.item())


def create_run(conn, run_id: str, model_version: str) -> None:
    """Create a running prediction run record."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO prediction_runs (run_id, model_version, status, cloudwatch_log_group, cloudwatch_log_stream)
            VALUES (%s, %s, 'RUNNING', %s, %s)
        """, (run_id, model_version, CLOUDWATCH_LOG_GROUP, CLOUDWATCH_LOG_STREAM))


def mark_processing(conn, images: list[dict]) -> None:
    """Mark selected images as currently being processed."""
    with conn.cursor() as cur:
        cur.execute("UPDATE images SET status = 'PROCESSING' WHERE image_id = ANY(%s)", ([i["image_id"] for i in images],))


def save_prediction(conn, image_id: str, run_id: str, model_version: str, predicted_class: str, confidence: float) -> None:
    """Store one prediction and mark the image as predicted."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO predictions (prediction_id, image_id, run_id, model_version, predicted_class, confidence)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (str(uuid.uuid4()), image_id, run_id, model_version, predicted_class, confidence))
        cur.execute("UPDATE images SET status = 'PREDICTED' WHERE image_id = %s", (image_id,))


def save_class_stats(conn, run_id: str, counts: Counter, total: int) -> None:
    """Store the prediction distribution for drift monitoring."""
    if total == 0:
        return
    with conn.cursor() as cur:
        for predicted_class, count in counts.items():
            cur.execute("""
                INSERT INTO prediction_class_stats (run_id, predicted_class, prediction_count, prediction_share)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id, predicted_class)
                DO UPDATE SET prediction_count = EXCLUDED.prediction_count,
                              prediction_share = EXCLUDED.prediction_share
            """, (run_id, predicted_class, count, count / total))


def mark_failed(conn, image_id: str) -> None:
    """Mark an image as failed after an inference error."""
    with conn.cursor() as cur:
        cur.execute("UPDATE images SET status = 'FAILED' WHERE image_id = %s", (image_id,))


def finish_run(conn, run_id: str, status: str, processed: int, failed: int, confidences: list[float], error: str | None = None) -> None:
    """Finish a prediction run and store aggregate monitoring statistics."""
    low_count = sum(c < LOW_CONFIDENCE_THRESHOLD for c in confidences)
    low_rate = low_count / len(confidences) if confidences else 0
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE prediction_runs
            SET status = %s,
                images_processed = %s,
                images_failed = %s,
                confidence_mean = %s,
                confidence_min = %s,
                confidence_max = %s,
                confidence_p05 = %s,
                confidence_p50 = %s,
                confidence_p95 = %s,
                low_confidence_count = %s,
                low_confidence_rate = %s,
                error_message = %s,
                finished_at = CURRENT_TIMESTAMP
            WHERE run_id = %s
        """, (
            status, processed, failed,
            sum(confidences) / len(confidences) if confidences else None,
            min(confidences) if confidences else None,
            max(confidences) if confidences else None,
            percentile(confidences, 0.05),
            percentile(confidences, 0.50),
            percentile(confidences, 0.95),
            low_count,
            low_rate,
            error,
            run_id,
        ))


def main() -> None:
    """Run batch prediction for all pending images in the configured limit."""
    run_id = str(uuid.uuid4())
    started = perf_counter()
    device = "cuda" if torch.cuda.is_available() else "cpu"

    with get_connection() as conn, tempfile.TemporaryDirectory() as tmpdir:
        cfg = load_yaml_from_s3(latest_prediction_config_key(conn))
        limit = int(cfg.get("batch", {}).get("limit", 1000))
        model_info = active_model(conn)
        images = pending_images(conn, limit)

        if not images:
            logger.info("No pending images found.")
            return

        create_run(conn, run_id, model_info["model_version"])
        mark_processing(conn, images)
        conn.commit()

        processed = failed = 0
        confidences: list[float] = []
        class_counts: Counter = Counter()

        try:
            model, class_names, image_size = load_model(model_info, device, Path(tmpdir))

            for image in images:
                path = Path(tmpdir) / image["filename"]
                try:
                    s3.download_file(S3_BUCKET, image["s3_key"], str(path))
                    pred_class, conf = predict(model, path, class_names, image_size, device)
                    save_prediction(conn, image["image_id"], run_id, model_info["model_version"], pred_class, conf)
                    processed += 1
                    confidences.append(conf)
                    class_counts[pred_class] += 1
                    logger.info("Predicted | image_id=%s class=%s confidence=%.4f", image["image_id"], pred_class, conf)
                except Exception:
                    logger.exception("Image prediction failed | image_id=%s", image["image_id"])
                    mark_failed(conn, image["image_id"])
                    failed += 1

                # Commit each image independently so a later failure keeps previous predictions.
                conn.commit()

            save_class_stats(conn, run_id, class_counts, processed)
            finish_run(conn, run_id, "SUCCESS", processed, failed, confidences)
            conn.commit()

        except Exception as exc:
            finish_run(conn, run_id, "FAILED", processed, failed + len(images) - processed - failed, confidences, str(exc))
            conn.commit()
            raise

    logger.info(
        "Batch prediction finished | run_id=%s processed=%s failed=%s confidence_mean=%s low_confidence_rate=%s runtime=%.2fs",
        run_id,
        processed,
        failed,
        round(sum(confidences) / len(confidences), 4) if confidences else None,
        round(sum(c < LOW_CONFIDENCE_THRESHOLD for c in confidences) / len(confidences), 4) if confidences else 0,
        perf_counter() - started,
    )


if __name__ == "__main__":
    main()