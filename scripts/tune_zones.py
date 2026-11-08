"""Offline zone tuning: replay a clip at the demo frame rate and report
per-zone count percentiles, dwell times, and which rules fire.

Usage: python scripts/tune_zones.py configs/demo.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config  # noqa: E402
from app.detector import PersonDetector  # noqa: E402
from app.rules import RuleEngine  # noqa: E402
from app.zones import ZoneEngine  # noqa: E402


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(ratio * (len(ordered) - 1))))
    return ordered[index]


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    config = load_config(config_path)
    source = str(Path(config["source"]))
    target_fps = float(config["video"].get("target_fps", 10))
    width = int(config["video"].get("width", 1280))

    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise SystemExit(f"cannot open {source}")
    source_fps = capture.get(cv2.CAP_PROP_FPS) or target_fps
    step = max(1, int(round(source_fps / target_fps)))

    detector = PersonDetector(
        config["detector"]["model"],
        conf=config["detector"]["conf"],
        iou=config["detector"]["iou"],
        min_box_height=config["detector"]["min_box_height"],
    )
    zones = ZoneEngine(config["zones"], pixels_per_meter=config["video"]["pixels_per_meter"])
    rules = RuleEngine(config["rules"])

    series: dict[str, list[int]] = {zone["name"]: [] for zone in config["zones"]}
    dwell: dict[str, list[float]] = {zone["name"]: [] for zone in config["zones"]}
    total_counts: list[int] = []
    events: list[dict] = []
    frames = 0
    started = time.time()

    while True:
        ok = True
        for _ in range(step - 1):
            ok = capture.grab() and ok
        ok, frame = capture.read()
        if not ok:
            break
        frame = cv2.resize(frame, (width, int(round(width * 9 / 16))), interpolation=cv2.INTER_AREA)
        height = frame.shape[0]

        tracks = detector.detect(frame)
        now = time.time()
        stats = zones.update(tracks, now, width, height)
        for name, zone_stats in stats.items():
            series[name].append(zone_stats.count)
            if zone_stats.max_dwell:
                dwell[name].append(zone_stats.max_dwell)
        total_counts.append(sum(s.count for s in stats.values()))
        for event in rules.evaluate(stats, now):
            events.append(
                {
                    "zone": event.zone,
                    "type": event.type,
                    "severity": event.severity,
                    "count": event.count,
                    "message": event.message,
                }
            )
        frames += 1

    capture.release()
    elapsed = time.time() - started

    print(f"file={Path(source).name} frames={frames} in {elapsed:.1f}s ({frames / elapsed:.1f} fps)")
    print(f"occupancy p50={percentile(total_counts, 0.5)} p90={percentile(total_counts, 0.9)} max={max(total_counts)}")
    for name in series:
        counts = series[name]
        print(
            f"  {name}: p50={percentile(counts, 0.5)} p90={percentile(counts, 0.9)} "
            f"max={max(counts)} capacity={next(z['capacity'] for z in config['zones'] if z['name'] == name)} "
            f"dwell_p90={percentile(dwell[name], 0.9):.1f}s dwell_max={max(dwell[name]) if dwell[name] else 0:.1f}s"
        )
    print(f"events={len(events)}")
    for event in events:
        print("   ", json.dumps(event))


if __name__ == "__main__":
    main()
