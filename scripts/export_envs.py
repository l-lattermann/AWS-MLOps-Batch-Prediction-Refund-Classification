import json
from pathlib import Path

infra_path = Path("terraform/infrastructure.json")
env_path = Path(".env")

with infra_path.open() as f:
    outputs = json.load(f)

env = outputs["app_environment"]["value"]
env["INFRASTRUCTURE_OUTPUT_PATH"] = "terraform/infrastructure.json"

env_path.write_text(
    "\n".join(f"{k}={v}" for k, v in sorted(env.items())) + "\n"
)

print("Wrote .env")
