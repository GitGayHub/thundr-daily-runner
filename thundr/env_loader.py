"""
Pure-Python .env file loader for Thundr.
Ensures project .env takes absolute priority over OS user environment variables,
preventing collisions with other user bots.
"""

import os
from pathlib import Path

def load_project_env(override: bool = True) -> None:
    """Parse .env file and update os.environ."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if override or key not in os.environ:
                        try:
                            os.environ[key] = val
                        except (ValueError, OSError):
                            # Skip variables exceeding OS limits (e.g. Windows 32767 chars)
                            pass
    except Exception:
        pass

# Automatically load on import
load_project_env(override=True)
