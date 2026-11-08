"""Sanity checks for the shipped JSON configs.

These guard the files a reviewer runs first: if a config loses a zone, a
normalized polygon point or the expected keys, the demo breaks at launch
rather than in a unit under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import BASE_DIR, load_config

CONFIG_DIR = BASE_DIR / "configs"
CONFIG_PATHS = sorted(CONFIG_DIR.glob("*.json"))


def _raw(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def test_configs_are_discoverable() -> None:
    names = {path.name for path in CONFIG_PATHS}
    assert {"demo.json", "normal.json", "any-clip.json"} <= names


@pytest.mark.parametrize("path", CONFIG_PATHS, ids=lambda path: path.name)
def test_config_loads_with_required_keys(path: Path) -> None:
    config = load_config(path)
    for key in ("source", "camera", "zones", "detector", "rules", "video", "storage"):
        assert key in config, f"{path.name} missing {key}"
    assert config["zones"], f"{path.name} has no zones"
    assert 0.0 < float(config["rules"]["warning_ratio"]) <= 1.0
    assert float(config["rules"]["cooldown_seconds"]) >= 0.0
    assert int(config["video"]["width"]) > 0
    assert float(config["video"]["target_fps"]) > 0


@pytest.mark.parametrize("path", CONFIG_PATHS, ids=lambda path: path.name)
def test_zone_polygons_are_normalized(path: Path) -> None:
    for zone in _raw(path)["zones"]:
        assert zone["name"], f"{path.name} has an unnamed zone"
        assert int(zone["capacity"]) > 0
        points = zone["polygon"]
        assert len(points) >= 3, f"{zone['name']} needs a polygon"
        for x, y in points:
            assert 0.0 <= float(x) <= 1.0, f"{zone['name']} x out of range"
            assert 0.0 <= float(y) <= 1.0, f"{zone['name']} y out of range"
        assert len(zone["color"]) == 3


def test_any_clip_config_covers_the_whole_frame() -> None:
    zones = _raw(CONFIG_DIR / "any-clip.json")["zones"]
    assert len(zones) == 1
    points = {(float(x), float(y)) for x, y in zones[0]["polygon"]}
    assert points == {(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)}
