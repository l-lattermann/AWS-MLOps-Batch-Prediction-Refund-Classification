import os

import psycopg

from storage.aws_secrets import get_secret


def get_connection_from_secret(
    secret_arn: str,
    region: str,
    host: str,
    port: int,
    dbname: str,
) -> psycopg.Connection:
    """Create a PostgreSQL connection using credentials from AWS Secrets Manager."""

    postgres = get_secret(secret_name=secret_arn, region=region)

    return psycopg.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=postgres["username"],
        password=postgres["password"],
    )


def get_connection() -> psycopg.Connection:
    """Create a PostgreSQL connection using environment configuration."""

    return get_connection_from_secret(
        secret_arn=os.environ["POSTGRES_SECRET_ARN"],
        region=os.environ["AWS_REGION"],
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        dbname=os.environ["POSTGRES_DB_NAME"],
    )