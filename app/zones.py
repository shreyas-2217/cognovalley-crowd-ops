"""Zone geometry, occupancy, density, dwell time, and movement speed.

This is plain geometry on top of detection boxes. No machine learning
happens here, which is why it is easy to explain and easy to test.
"""

# TEACHING NOTE: defers evaluation of type hints (list[...], X | None).
from __future__ import annotations

import math                     # math.hypot for straight-line distance
from dataclasses import dataclass   # @dataclass auto-writes __init__ for us
from typing import Iterable, Sequence   # type hints only; they do nothing at runtime

import cv2                      # pointPolygonTest and contourArea
import numpy as np              # OpenCV needs polygons as numpy arrays


@dataclass
class Track:
    """One detected person for one frame.

    A plain container. x1,y1 is the top-left corner of the box and x2,y2 the
    bottom-right, in pixels of the letterboxed frame.
    """

    id: int         # tracker ID from ByteTrack; same person keeps the same id
    x1: float       # left edge, pixels
    y1: float       # top edge, pixels
    x2: float       # right edge, pixels
    y2: float       # bottom edge, pixels

    @property
    def cx(self) -> float:
        """Horizontal centre of the box, in pixels."""
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        """Vertical centre of the box, in pixels."""
        return (self.y1 + self.y2) / 2.0


@dataclass
class Zone:
    """One configured area of interest.

    `polygon` stores NORMALISED points (each 0..1), not pixels, so the same
    config works on any resolution.
    """

    name: str                               # e.g. "West lane"
    polygon: list[tuple[float, float]]      # [(x, y), ...] each 0..1
    capacity: int                           # how many people the zone is designed for


@dataclass
class ZoneStats:
    """The numbers we compute for one zone on one frame."""

    name: str           # zone name, copied through
    count: int          # people currently inside
    capacity: int       # from config, copied through
    density: float      # people per square metre
    max_dwell: float    # longest time any one person has been inside, seconds
    velocity: float     # average speed of people inside, metres per second


def point_in_polygon(x: float, y: float, polygon_px: Sequence[tuple[int, int]]) -> bool:
    """Return True when (x, y) is inside or on the polygon boundary.

    Extracted as a free function so it can be unit-tested without a ZoneEngine.
    """
    # A polygon needs at least 3 corners; fewer is not an area, so nothing is inside.
    if len(polygon_px) < 3:
        return False
    # OpenCV wants an int32 numpy array of shape (N, 2).
    contour = np.array(polygon_px, dtype=np.int32)
    # >= 0 means "inside (1) or exactly on the edge (0)". Using > 0 would make
    # a person standing exactly on a boundary line count as outside.
    return cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0


class ZoneEngine:
    """Turns per-frame tracks into per-zone operational metrics."""

    def __init__(self, zones: Iterable[dict], pixels_per_meter: float = 45.0) -> None:
        # Build Zone objects once at startup from the config dicts.
        self.zones = [
            Zone(
                name=str(zone["name"]),
                # Turn the JSON lists into tuples of floats.
                polygon=[(float(px), float(py)) for px, py in zone["polygon"]],
                # .get with a default keeps a missing capacity from crashing startup.
                capacity=int(zone.get("capacity", 10)),
            )
            for zone in zones
        ]
        # One number for the whole frame: how many pixels equal one metre.
        # Used for both speed and density. Rough, not perspective-correct.
        self.pixels_per_meter = float(pixels_per_meter)
        # (track_id, zone_name) -> time the track first entered the zone
        # The zone name is part of the key so one person can be timed in two
        # overlapping zones independently.
        self._entry: dict[tuple[int, str], float] = {}
        # track_id -> (cx, cy, timestamp) of the last frame we saw them.
        # This is the reference point for speed.
        self._last_pos: dict[int, tuple[float, float, float]] = {}

    def reset(self) -> None:
        """Forget dwell and speed history (used when a clip loops)."""
        # Called when the video restarts, so last-pass timers do not leak in.
        self._entry.clear()
        self._last_pos.clear()

    def _polygons_px(self, frame_w: int, frame_h: int) -> dict[str, list[tuple[int, int]]]:
        """Convert every normalised polygon to pixel coordinates for this frame."""
        return {
            zone.name: [
                # px is 0..1 of the width, py is 0..1 of the height.
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
        # Drop detections the tracker could not identify (id -1).
        # Dwell and speed are keyed on id, so an unidentified box is useless here.
        tracks = [track for track in tracks if track.id >= 0]
        # Scale polygons from 0..1 into this frame's pixels.
        polygons = self._polygons_px(frame_w, frame_h)
        # Every (id, zone) pair seen this frame; used at the end to spot leavers.
        present_keys: set[tuple[int, str]] = set()
        stats: dict[str, ZoneStats] = {}

        for zone in self.zones:
            polygon = polygons[zone.name]
            # "inside" = people whose box CENTRE falls in this polygon.
            # Centre (not feet, not whole box) is a deliberate simplification.
            inside = [
                track
                for track in tracks
                if point_in_polygon(track.cx, track.cy, polygon)
            ]

            # --- dwell: how long each person has been in this zone ---
            dwells: list[float] = []
            for track in inside:
                key = (track.id, zone.name)
                present_keys.add(key)       # remember: this pair is still here
                if key not in self._entry:
                    # First frame we see them in this zone: start their clock.
                    self._entry[key] = now
                # elapsed = now minus when they entered. max(0.0, ...) guards
                # against a negative value if timestamps ever go backwards.
                dwells.append(max(0.0, now - self._entry[key]))

            # --- speed: displacement between last frame and this one ---
            speeds: list[float] = []
            for track in inside:
                previous = self._last_pos.get(track.id)
                if previous is None:
                    # First time we have ever seen this id: no reference point yet.
                    continue
                prev_x, prev_y, prev_t = previous
                dt = now - prev_t
                if dt <= 0:
                    # Two frames with the same timestamp would divide by zero.
                    # (Happens if the loop runs faster than the clock resolution.)
                    continue
                # Straight-line distance moved, in pixels.
                distance = math.hypot(track.cx - prev_x, track.cy - prev_y)
                # pixels/second, then divided by pixels_per_meter -> metres/second.
                speeds.append((distance / dt) / self.pixels_per_meter)

            # --- density: people divided by the zone's real-world area ---
            # contourArea returns the polygon's area in square pixels.
            # abs() because OpenCV's sign depends on point winding order.
            area_px = abs(cv2.contourArea(np.array(polygon, dtype=np.int32)))
            # Divide by (px per metre)^2 to convert square pixels to square metres.
            # The guard avoids dividing by zero if the scale is misconfigured.
            area_m2 = area_px / (self.pixels_per_meter**2) if self.pixels_per_meter > 0 else 0.0
            # People per square metre.
            density = len(inside) / area_m2 if area_m2 > 0 else 0.0

            stats[zone.name] = ZoneStats(
                name=zone.name,
                count=len(inside),                      # occupancy for this frame
                capacity=zone.capacity,
                density=round(density, 2),              # 2 decimals is enough for a UI
                # Longest current dwell. max() of an empty list would crash,
                # hence the `if dwells else 0.0`.
                max_dwell=round(max(dwells), 1) if dwells else 0.0,
                # Average speed, same empty-list guard.
                velocity=round(sum(speeds) / len(speeds), 2) if speeds else 0.0,
            )

        # Remember every track's position for next frame's speed calculation.
        # Done for ALL tracks, not just those inside a zone, so speed is correct
        # on the frame a person steps into a zone.
        for track in tracks:
            self._last_pos[track.id] = (track.cx, track.cy, now)

        # A track that left a zone loses its entry timer, so re-entry restarts dwell.
        # We iterate over a list(...) copy because we delete while looping.
        for key in list(self._entry):
            if key not in present_keys:
                del self._entry[key]

        # Drop stale positions so memory does not grow across a long demo.
        # If an id has not been seen for 3 seconds, the tracker has forgotten it.
        for track_id, (_, _, seen_at) in list(self._last_pos.items()):
            if now - seen_at > 3.0:
                del self._last_pos[track_id]

        return stats
