from __future__ import annotations

import os
from pathlib import Path


def load_local_env(path: str | Path = ".env") -> bool:
    """Load a small local .env file without adding another runtime dependency."""
    env_path = Path(path)
    if not env_path.is_file():
        return False
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
    return True
