import json
import logging
from pathlib import Path

from app.logging_config import setup_logger

setup_logger(__name__)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
INFRA_PATH = ROOT / "terraform" / "infrastructure.json"
ENV_PATH = ROOT / ".env"


def main() -> None:
    """Generate the project .env file from Terraform outputs."""

    logger.info("Loading infrastructure outputs | path=%s", INFRA_PATH)

    with INFRA_PATH.open(encoding="utf-8") as f:
        outputs = json.load(f)

    env = outputs["app_environment"]["value"]
    env["INFRASTRUCTURE_OUTPUT_PATH"] = "terraform/infrastructure.json"

    ENV_PATH.write_text(
        "\n".join(f"{k}={v}" for k, v in sorted(env.items())) + "\n",
        encoding="utf-8",
    )

    logger.info("Wrote environment file | path=%s", ENV_PATH)


if __name__ == "__main__":
    main()