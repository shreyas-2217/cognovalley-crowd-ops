"""Zone geometry, occupancy, density, dwell time, and movement speed.

This is plain geometry on top of detection boxes. No machine learning
happens here, which is why it is easy to explain and easy to test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import cv2
import numpy as np


@dataclass
class Track:
    """One detected person for one frame."""

    id: int
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0


@dataclass
class Zone:
    name: str
    polygon: list[tuple[float, float]]
    capacity: int


@dataclass
class ZoneStats:
    name: str
    count: int
    capacity: int
    density: float
    max_dwell: float
    velocity: float


def point_in_polygon(x: float, y: float, polygon_px: Sequence[tuple[int, int]]) -> bool:
    """Return True when (x, y) is inside or on the polygon boundary."""
    if len(polygon_px) < 3:
        return False
    contour = np.array(polygon_px, dtype=np.int32)
    return cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0


class ZoneEngine:
    """Turns per-frame tracks into per-zone operational metrics."""

    def __init__(self, zones: Iterable[dict], pixels_per_meter: float = 45.0) -> None:
        self.zones = [
            Zone(
                name=str(zone["name"]),
                polygon=[(float(px), float(py)) for px, py in zone["polygon"]],
                capacity=int(zone.get("capacity", 10)),
            )
            for zone in zones
        ]
        self.pixels_per_meter = float(pixels_per_meter)
        # (track_id, zone_name) -> time the track first entered the zone
        self._entry: dict[tuple[int, str], float] = {}
        # track_id -> (cx, cy, timestamp)
        self._last_pos: dict[int, tuple[float, float, float]] = {}

    def reset(self) -> None:
        """Forget dwell and speed history (used when a clip loops)."""
        self._entry.clear()
        self._last_pos.clear()

    def _polygons_px(self, frame_w: int, frame_h: int) -> dict[str, list[tuple[int, int]]]:
        return {
            zone.name: [
                (int(round(px * frame_w)), int(round(py * frame_h)))
                for px, py in zone.polygon
            ]
            for zone in self.zones
        }

    def update(
        self,
        tracks: list[Track],
        now: float,
        frame_w: int,
        frame_h: int,
    ) -> dict[str, ZoneStats]:
        """Compute occupancy, density, dwell, and speed for every zone."""
        tracks = [track for track in tracks if track.id >= 0]
        polygons = self._polygons_px(frame_w, frame_h)
        present_keys: set[tuple[int, str]] = set()
        stats: dict[str, ZoneStats] = {}

        for zone in self.zones:
            polygon = polygons[zone.name]
            inside = [
                track
                for track in tracks
                if point_in_polygon(track.cx, track.cy, polygon)
            ]

            dwells: list[float] = []
            for track in inside:
                key = (track.id, zone.name)
                present_keys.add(key)
                if key not in self._entry:
                    self._entry[key] = now
                dwells.append(max(0.0, now - self._entry[key]))

            speeds: list[float] = []
            for track in inside:
                previous = self._last_pos.get(track.id)
                if previous is None:
                    continue
                prev_x, prev_y, prev_t = previous
                dt = now - prev_t
                if dt <= 0:
                    continue
                distance = math.hypot(track.cx - prev_x, track.cy - prev_y)
                speeds.append((distance / dt) / self.pixels_per_meter)

            area_px = abs(cv2.contourArea(np.array(polygon, dtype=np.int32)))
            area_m2 = area_px / (self.pixels_per_meter**2) if self.pixels_per_meter > 0 else 0.0
            density = len(inside) / area_m2 if area_m2 > 0 else 0.0

            stats[zone.name] = ZoneStats(
                name=zone.name,
                count=len(inside),
                capacity=zone.capacity,
                density=round(density, 2),
                max_dwell=round(max(dwells), 1) if dwells else 0.0,
                velocity=round(sum(speeds) / len(speeds), 2) if speeds else 0.0,
            )

        for track in tracks:
            self._last_pos[track.id] = (track.cx, track.cy, now)

        # A track that left a zone loses its entry timer, so re-entry restarts dwell.
        for key in list(self._entry):
            if key not in present_keys:
                del self._entry[key]

        # Drop stale positions so memory does not grow across a long demo.
        for track_id, (_, _, seen_at) in list(self._last_pos.items()):
            if now - seen_at > 3.0:
                del self._last_pos[track_id]

        return stats
