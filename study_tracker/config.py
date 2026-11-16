"""Carga de configuración y rutas del proyecto."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
EXTENSION_DIR = ROOT / "extension"


def load_config(path: Path = CONFIG_PATH) -> dict:
    # PyYAML ya convierte "2026-12-10" en datetime.date
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
