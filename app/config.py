"""Configuration defaults and a small JSON override loader.

Every tunable value lives here so the panel can see that zones and
thresholds are configuration, not hard-coded magic numbers.
"""

# TEACHING NOTE: defers evaluation of type hints like dict[str, Any].
from __future__ import annotations

import copy     # deepcopy so we never mutate the shared default dict
import json     # read the override files in configs/
from pathlib import Path    # cross-platform path handling
from typing import Any      # type hint only

# __file__ is app/config.py. .parent is app/, .parent again is the project root.
# Because it is derived from this file's location, the app works no matter
# which directory the shell happens to be sitting in.
BASE_DIR = Path(__file__).resolve().parent.parent


# A complete, working configuration. If no JSON file is supplied, the app still
# runs on these values. Shipped configs override pieces of this.
DEFAULT_CONFIG: dict[str, Any] = {
    # Video file to analyse, relative to BASE_DIR.
    "source": "data/clips/demo-crowd.webm",
    # Free-text camera label shown in the dashboard header.
    "camera": {"name": "Camera 01 · Pedestrian street"},
    # Each zone: a name, a NORMALISED polygon (0..1 of width/height), capacity.
    "zones": [
        {
            "name": "Gate A",
            "polygon": [[0.04, 0.30], [0.34, 0.30], [0.40, 0.96], [0.02, 0.96]],
            "capacity": 12,
        },
        {
            "name": "Corridor",
            "polygon": [[0.37, 0.30], [0.66, 0.30], [0.72, 0.96], [0.33, 0.96]],
            "capacity": 18,
        },
        {
            "name": "Gate B",
            "polygon": [[0.69, 0.30], [0.97, 0.30], [0.98, 0.96], [0.63, 0.96]],
            "capacity": 12,
        },
    ],
    # Detector settings, passed straight to PersonDetector.
    "detector": {
        "model": "yolo11n.pt",   # weight file name
        "conf": 0.40,            # minimum confidence
        "iou": 0.50,             # NMS overlap threshold
        "min_box_height": 36,    # ignore boxes shorter than this (distant/noise)
    },
    # Rule thresholds, passed straight to RuleEngine.
    "rules": {
        "warning_ratio": 0.80,   # warn at 80% of capacity
        "alert_seconds": 3.0,    # condition must hold this long
        "dwell_seconds": 30.0,   # dwell event threshold
        "cooldown_seconds": 15.0,  # min gap between same-kind events
    },
    # Output video settings.
    "video": {
        "width": 1280,              # letterboxed output width (height is derived 16:9)
        "jpeg_quality": 82,         # MJPEG compression quality
        "target_fps": 12,           # cap on processed frames per second
        "pixels_per_meter": 45,     # scale used for speed and density
    },
    # Where state is written.
    "storage": {
        "db_path": "cognovalley.db",        # SQLite file
        "snapshot_dir": "data/snapshots",   # evidence JPEGs
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into base without mutating either input."""
    # Start from a full copy so the module-level DEFAULT_CONFIG is never touched.
    result = copy.deepcopy(base)
    for key, value in override.items():
        # If both sides are dicts, merge recursively so you only need to specify
        # the keys you want to change (e.g. just rules.cooldown_seconds).
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            # Anything else replaces wholesale. deepcopy so the caller's dict
            # cannot be mutated through the result later.
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Return defaults, optionally overridden by a JSON file."""
    # Always begin with a private copy of the defaults.
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path is not None:
        config_path = Path(path)
        # A relative path is interpreted from the project root, not the cwd,
        # so "configs/demo.json" works from anywhere.
        if not config_path.is_absolute():
            config_path = BASE_DIR / config_path
        with open(config_path, "r", encoding="utf-8") as handle:
            config = _deep_merge(config, json.load(handle))
    return config


def resolve_path(value: str | Path) -> Path:
    """Resolve a config path against the project root."""
    path = Path(value)
    # Absolute paths are respected as-is; relative ones are anchored to BASE_DIR.
    return path if path.is_absolute() else BASE_DIR / path
