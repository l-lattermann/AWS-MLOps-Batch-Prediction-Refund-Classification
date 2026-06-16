import os

import psycopg

from storage.aws_secrets import get_secret


def get_connection() -> psycopg.Connection:
    app_config = get_secret(
        secret_name=os.environ["APP_CONFIG_SECRET_NAME"],
        region=os.environ["AWS_REGION"],
    )

    postgres_secret = get_secret(
        secret_name=app_config["postgres_secret_name"],
        region=app_config["aws_region"],
    )

    return psycopg.connect(
        host=postgres_secret["host"],
        port=postgres_secret["port"],
        dbname=postgres_secret["database"],
        user=postgres_secret["username"],
        password=postgres_secret["password"],
    )