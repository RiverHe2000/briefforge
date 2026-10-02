from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    path = Path(os.getenv("BRIEFFORGE_DATA_DIR", str(ROOT / ".local"))).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    return os.getenv("BRIEFFORGE_DATABASE_URL", f"sqlite:///{(data_dir() / 'briefforge.db').as_posix()}")
