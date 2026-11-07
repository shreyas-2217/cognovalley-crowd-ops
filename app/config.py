"""Configuration defaults and a small JSON override loader.

Every tunable value lives here so the panel can see that zones and
thresholds are configuration, not hard-coded magic numbers.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    "source": "data/clips/demo-crowd.webm",
    "camera": {"name": "Camera 01 · Pedestrian street"},
    "zones": [
        {
            "name": "Gate A",
            "polygon": [[0.04, 0.30], [0.34, 0.30], [0.40, 0.96], [0.02, 0.96]],
            "capacity": 12,
            "color": [34, 211, 238],
        },
        {
            "name": "Corridor",
            "polygon": [[0.37, 0.30], [0.66, 0.30], [0.72, 0.96], [0.33, 0.96]],
            "capacity": 18,
            "color": [56, 189, 248],
        },
        {
            "name": "Gate B",
            "polygon": [[0.69, 0.30], [0.97, 0.30], [0.98, 0.96], [0.63, 0.96]],
            "capacity": 12,
            "color": [45, 212, 191],
        },
    ],
    "detector": {
        "model": "yolo11n.pt",
        "conf": 0.40,
        "iou": 0.50,
        "min_box_height": 36,
    },
    "rules": {
        "warning_ratio": 0.80,
        "alert_seconds": 3.0,
        "dwell_seconds": 30.0,
        "cooldown_seconds": 15.0,
    },
    "video": {
        "width": 1280,
        "jpeg_quality": 82,
        "target_fps": 12,
        "pixels_per_meter": 45,
    },
    "storage": {
        "db_path": "cognovalley.db",
        "snapshot_dir": "data/snapshots",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into base without mutating either input."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Return defaults, optionally overridden by a JSON file."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path is not None:
        config_path = Path(path)
        if not config_path.is_absolute():
            config_path = BASE_DIR / config_path
        with open(config_path, "r", encoding="utf-8") as handle:
            config = _deep_merge(config, json.load(handle))
    return config


def resolve_path(value: str | Path) -> Path:
    """Resolve a config path against the project root."""
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path
