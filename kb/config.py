import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def integer(name, default):
    return int(os.getenv(name, str(default)))


def data_dir():
    path = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def model():
    value = os.getenv("MODEL", "").strip()
    if not value:
        raise ValueError("Set MODEL to an available OpenRouter model ID")
    return value
