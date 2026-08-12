"""Tiny ``.env`` loader so keys do not have to be exported in every terminal.

Deliberately dependency-free and non-destructive: a variable that is already set
in the environment always wins, so ``$env:FOOTBALLDATA_API_KEY`` still overrides
the file. The file itself is git-ignored.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Read ``KEY=value`` lines into ``os.environ`` and return what was applied."""
    env_path = path or ENV_FILE
    if not env_path.exists():
        return {}

    applied: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
