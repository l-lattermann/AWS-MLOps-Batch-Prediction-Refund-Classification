import logging
from pathlib import Path

from dotenv import load_dotenv

from app.logging_config import setup_logger
from storage.rds_connection import get_connection


setup_logger(__name__)
logger = logging.getLogger(__name__)

SCHEMA_PATH = Path("sql/01_schema.sql")


def load_schema_sql() -> str:
    logger.info("Loading schema file | path=%s", SCHEMA_PATH)
    return SCHEMA_PATH.read_text()


def initialize_database() -> None:
    logger.info("Connecting to PostgreSQL")

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(load_schema_sql())
        conn.commit()

    logger.info("Database schema initialized successfully")


def main() -> None:
    load_dotenv()

    logger.info("Starting database initialization")
    initialize_database()
    logger.info("Database initialization finished")


if __name__ == "__main__":
    main()