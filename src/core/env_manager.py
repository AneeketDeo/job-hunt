from pathlib import Path
from typing import Dict, Optional
import os

from config.settings import settings
from src.core.logger import logger


def update_env_file(updates: Dict[str, Optional[str]]) -> None:
    """Safely update or insert key-value pairs in the project's .env file."""
    env_path = settings.PROJECT_ROOT / ".env"
    example_path = settings.PROJECT_ROOT / ".env.example"

    # If .env does not exist, copy from .env.example
    if not env_path.exists() and example_path.exists():
        with open(example_path, "r", encoding="utf-8") as f:
            content = f.read()
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(content)

    existing_lines = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            existing_lines = f.readlines()

    updated_keys = set()
    new_lines = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in updates:
                val = updates[key] or ""
                new_lines.append(f"{key}={val}\n")
                updated_keys.add(key)
                # Update in-memory os.environ
                os.environ[key] = val
                continue
        new_lines.append(line)

    # Append any keys that weren't found in the existing file
    for key, val in updates.items():
        if key not in updated_keys and val is not None:
            new_lines.append(f"{key}={val}\n")
            os.environ[key] = val

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    logger.info(f"Updated {len(updates)} environment keys in {env_path}")
