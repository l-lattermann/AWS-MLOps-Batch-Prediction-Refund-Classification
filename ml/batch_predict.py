import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from time import perf_counter

import boto3
import torch
from PIL import Image
from torchvision import transforms

from app.logging_config import setup_logger
from ml.model import create_model
from storage.rds_connection import get_connection

setup_logger(__name__)
logger = logging.getLogger(__name__)

AWS_REGION = os.environ["AWS_REGION"]
S3_BUCKET = os.environ["S3_BUCKET"]
CLOUDWATCH_LOG_GROUP = os.environ.get("CLOUDWATCH_LOG_GROUP")
CLOUDWATCH_LOG_STREAM = os.environ.get("CLOUDWATCH_LOG_STREAM")

s3 = boto3.client("s3", region_name=AWS_REGION)


def get_active_model(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT model_version, architecture, s3_model_path, s3_metadata_path
            FROM models
            WHERE active = TRUE
            ORDER BY created_at DESC
            LIMIT 1;
            """
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("No active model found.")

    return {
        "model_version": row[0],
        "architecture": row[1],
        "s3_model_path": row[2],
        "s3_metadata_path": row[3],
    }


def get_pending_images(conn, limit: int = 1000) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT image_id, s3_key, filename
            FROM images
            WHERE status = 'PENDING'
            ORDER BY uploaded_at ASC
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()

    return [{"image_id": row[0], "s3_key": row[1], "filename": row[2]} for row in rows]


def create_prediction_run(conn, run_id: str, model_version: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO prediction_runs (
                run_id, model_version, status, cloudwatch_log_group, cloudwatch_log_stream
            )
            VALUES (%s, %s, 'RUNNING', %s, %s);
            """,
            (run_id, model_version, CLOUDWATCH_LOG_GROUP, CLOUDWATCH_LOG_STREAM),
        )
    conn.commit()


def mark_images_processing(conn, images: list[dict]) -> None:
    if not images:
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE images
            SET status = 'PROCESSING'
            WHERE image_id = ANY(%s);
            """,
            ([image["image_id"] for image in images],),
        )
    conn.commit()


def download_s3_file(key: str, local_path: Path) -> None:
    logger.info("Downloading s3://%s/%s -> %s", S3_BUCKET, key, local_path)
    s3.download_file(S3_BUCKET, key, str(local_path))


def load_model_metadata(s3_metadata_path: str) -> dict:
    response = s3.get_object(Bucket=S3_BUCKET, Key=s3_metadata_path)
    return json.loads(response["Body"].read().decode("utf-8"))


def load_model_from_s3(model_info: dict, device: str) -> tuple[torch.nn.Module, list[str], int]:
    metadata = load_model_metadata(model_info["s3_metadata_path"])
    class_names = metadata["class_names"]
    image_size = int(metadata["image_size"])

    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = Path(tmpdir) / Path(model_info["s3_model_path"]).name
        download_s3_file(model_info["s3_model_path"], model_path)

        model = create_model(
            num_classes=len(class_names),
            architecture=model_info["architecture"],
            pretrained=False,
        )

        checkpoint = torch.load(model_path, map_location=device)

        if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
            model.load_state_dict(checkpoint["model_state_dict"])
        else:
            model.load_state_dict(checkpoint)

        model.to(device)
        model.eval()

    return model, class_names, image_size


def get_transform(image_size: int) -> transforms.Compose:
    return transforms.Compose([
        transforms.Resize((image_size, image_size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])


def predict_image(model, image_path: Path, class_names: list[str], image_size: int, device: str) -> tuple[str, float]:
    image = Image.open(image_path).convert("RGB")
    tensor = get_transform(image_size)(image).unsqueeze(0).to(device)

    with torch.no_grad():
        probabilities = torch.softmax(model(tensor), dim=1)[0]
        confidence, class_idx = torch.max(probabilities, dim=0)

    return class_names[int(class_idx.item())], float(confidence.item())


def upsert_prediction(
    conn,
    prediction_id: str,
    image_id: str,
    run_id: str,
    model_version: str,
    predicted_class: str,
    confidence: float,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO predictions (
                prediction_id, image_id, run_id, model_version, predicted_class, confidence
            )
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (prediction_id, image_id, run_id, model_version, predicted_class, confidence),
        )


def mark_image_predicted(conn, image_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE images
            SET status = 'PREDICTED'
            WHERE image_id = %s;
            """,
            (image_id,),
        )


def mark_image_failed(conn, image_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE images
            SET status = 'FAILED'
            WHERE image_id = %s;
            """,
            (image_id,),
        )


def mark_prediction_run_success(conn, run_id: str, images_processed: int, images_failed: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE prediction_runs
            SET status = 'SUCCESS',
                images_processed = %s,
                images_failed = %s,
                finished_at = CURRENT_TIMESTAMP
            WHERE run_id = %s;
            """,
            (images_processed, images_failed, run_id),
        )
    conn.commit()


def mark_prediction_run_failed(conn, run_id: str, error_message: str, images_processed: int, images_failed: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE prediction_runs
            SET status = 'FAILED',
                images_processed = %s,
                images_failed = %s,
                error_message = %s,
                finished_at = CURRENT_TIMESTAMP
            WHERE run_id = %s;
            """,
            (images_processed, images_failed, error_message, run_id),
        )
    conn.commit()


def run_predictions(conn, model, model_version: str, class_names: list[str], image_size: int, images: list[dict], run_id: str, device: str) -> tuple[int, int]:
    processed = failed = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        for image in images:
            local_path = tmpdir_path / image["filename"]

            try:
                download_s3_file(image["s3_key"], local_path)
                predicted_class, confidence = predict_image(model, local_path, class_names, image_size, device)

                upsert_prediction(
                    conn=conn,
                    prediction_id=str(uuid.uuid4()),
                    image_id=image["image_id"],
                    run_id=run_id,
                    model_version=model_version,
                    predicted_class=predicted_class,
                    confidence=confidence,
                )
                mark_image_predicted(conn, image["image_id"])
                conn.commit()

                processed += 1
                logger.info(
                    "Prediction finished | image_id=%s class=%s confidence=%.4f",
                    image["image_id"],
                    predicted_class,
                    confidence,
                )

            except Exception:
                logger.exception("Prediction failed | image_id=%s s3_key=%s", image["image_id"], image["s3_key"])
                mark_image_failed(conn, image["image_id"])
                conn.commit()
                failed += 1

    return processed, failed


def main() -> None:
    run_id = str(uuid.uuid4())
    device = "cuda" if torch.cuda.is_available() else "cpu"
    t_start = perf_counter()

    with get_connection() as conn:
        model_info = get_active_model(conn)
        images = get_pending_images(conn)

        if not images:
            logger.info("No pending images found.")
            return

        create_prediction_run(conn, run_id, model_info["model_version"])
        mark_images_processing(conn, images)

        try:
            logger.info(
                "Batch prediction started | run_id=%s model_version=%s images=%s device=%s",
                run_id,
                model_info["model_version"],
                len(images),
                device,
            )

            model, class_names, image_size = load_model_from_s3(model_info, device)
            processed, failed = run_predictions(
                conn=conn,
                model=model,
                model_version=model_info["model_version"],
                class_names=class_names,
                image_size=image_size,
                images=images,
                run_id=run_id,
                device=device,
            )

            mark_prediction_run_success(conn, run_id, processed, failed)
            logger.info(
                "Batch prediction finished | run_id=%s processed=%s failed=%s runtime=%.2fs",
                run_id,
                processed,
                failed,
                perf_counter() - t_start,
            )

        except Exception as exc:
            logger.exception("Batch prediction job failed | run_id=%s", run_id)
            mark_prediction_run_failed(conn, run_id, str(exc), 0, len(images))
            raise


if __name__ == "__main__":
    main()