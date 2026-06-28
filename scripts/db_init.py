import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from app.logging_config import setup_logger
from storage.rds_connection import get_connection_from_secret

setup_logger(__name__)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "sql" / "01_schema.sql"


def load_schema_sql() -> str:
    logger.info("Loading schema file | path=%s", SCHEMA_PATH)
    return SCHEMA_PATH.read_text(encoding="utf-8")


def load_infra_environment() -> dict[str, str]:
    path = (ROOT / os.environ["INFRASTRUCTURE_OUTPUT_PATH"]).resolve()
    logger.info("Loading infrastructure outputs | path=%s", path)

    with path.open(encoding="utf-8") as f:
        outputs = json.load(f)

    return outputs["app_environment"]["value"]


def initialize_database() -> None:
    infra_env = load_infra_environment()

    logger.info("Connecting to PostgreSQL")

    with get_connection_from_secret(
        secret_arn=infra_env["POSTGRES_SECRET_ARN"],
        region=infra_env["AWS_REGION"],
        host=infra_env["POSTGRES_HOST"],
        port=int(infra_env["POSTGRES_PORT"]),
        dbname=infra_env["POSTGRES_DB_NAME"],
    ) as conn:
        with conn.cursor() as cursor:
            cursor.execute(load_schema_sql())
        conn.commit()

    logger.info("Database schema initialized successfully")


def main() -> None:
    load_dotenv(ROOT / ".env")

    logger.info("Starting database initialization")
    initialize_database()
    logger.info("Database initialization finished")


if __name__ == "__main__":
    main()