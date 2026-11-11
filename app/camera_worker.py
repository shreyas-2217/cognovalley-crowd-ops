"""Background inference worker."""

from __future__ import annotations

import copy
import math
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from app.config import resolve_path
from app.detector import PersonDetector
from app.rules import Event, RuleEngine
from app.storage import EventStore
from app.zones import Track, ZoneEngine

HEAT_COLS = 32
HEAT_ROWS = 18
# seconds for a heat cell to fade to 1/e
HEAT_TAU_SECONDS = 4.0
BOX_COLOR_BGR = (238, 211, 34)
TEXT_COLOR = (240, 248, 255)


class CameraWorker:
    """Owns the capture->detect->measure->publish loop on a background thread."""

    def __init__(
        self,
        config: dict,
        detector: PersonDetector,
        zone_engine: ZoneEngine,
        rule_engine: RuleEngine,
        store: EventStore,
    ) -> None:
        self.config = config
        self.detector = detector
        self.zone_engine = zone_engine
        self.rule_engine = rule_engine
        self.store = store

        self.source = str(resolve_path(config["source"]))
        self.snapshot_dir = resolve_path(config["storage"]["snapshot_dir"])
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

        # one lock for published state; never held during inference
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # published state: latest JPEG, frame id, fps, stats
        self._jpeg: bytes | None = None
        self._frame_id = 0
        self._fps = 0.0
        # seed from the database so a restart shows the real count
        self._events_total = store.count()
        self._heat = np.zeros((HEAT_ROWS, HEAT_COLS), dtype=np.float32)
        self._heat_at: float | None = None
        # a valid payload before the first frame, so the API never fails early
        self._stats = self._empty_stats("starting")

    def _empty_stats(self, status: str) -> dict:
        """Build a valid stats payload with zeroed values for every zone."""
        zones = [
            {
                "name": zone["name"],
                "count": 0,
                "capacity": int(zone.get("capacity", 10)),
                "density": 0.0,
                "max_dwell": 0.0,
                "velocity": 0.0,
                "status": "ok",
                "polygon": zone["polygon"],
            }
            for zone in self.config["zones"]
        ]
        return {
            "ts": time.time(),
            "source": Path(self.source).name,
            "camera": self.config.get("camera", {}).get("name", "Camera 01"),
            "status": status,
            "fps": 0.0,
            "frame": {"w": 0, "h": 0},
            "occupancy": 0,
            "zones": zones,
            "heatmap": {
                "cols": HEAT_COLS,
                "rows": HEAT_ROWS,
                "values": [0.0] * (HEAT_COLS * HEAT_ROWS),
            },
            "events_total": self._events_total,
        }

    def start(self) -> None:
        """Start the background loop if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        # daemon: a hung loop cannot keep the process alive after Ctrl+C
        self._thread = threading.Thread(target=self._run, name="camera-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Ask the loop to finish and wait briefly for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def get_frame_with_id(self) -> tuple[int, bytes | None]:
        """Return (frame counter, latest JPEG) atomically."""
        with self._lock:
            # the counter lets /video detect 'nothing new'
            return self._frame_id, self._jpeg

    def get_stats(self) -> dict:
        """Return a deep copy so callers cannot mutate worker state."""
        with self._lock:
            return copy.deepcopy(self._stats)

    def reset_events_total(self) -> None:
        """Zero the session counter after a Clear."""
        with self._lock:
            self._events_total = 0
            self._stats = copy.deepcopy(self._stats)
            self._stats["events_total"] = 0

    def _add_events(self, count: int) -> None:
        """Count new events under the same lock that reset_events_total() uses."""
        if count <= 0:
            return
        with self._lock:
            self._events_total += count

    def _set_state(self, jpeg: bytes, stats: dict) -> None:
        """Publish a new frame and its stats atomically."""
        with self._lock:
            self._jpeg = jpeg
            # one atomic publish: readers never see a new frame with old stats
            self._frame_id += 1
            self._stats = stats

    def _set_status(self, status: str) -> None:
        """Change only the status field, keeping the rest of the stats."""
        with self._lock:
            self._stats = copy.deepcopy(self._stats)
            self._stats["status"] = status

    def _resize(self, frame: np.ndarray, target_width: int) -> np.ndarray:
        """Letterbox to 16:9 so browser overlays always line up with the frame."""
        height, width = frame.shape[:2]
        target_height = int(round(target_width * 9 / 16))
        if width == 0 or height == 0:
            return frame

        # fit inside the box without distorting; the leftover is the letterbox
        scale = min(target_width / width, target_height / height)
        new_w = max(1, int(round(width * scale)))
        new_h = max(1, int(round(height * scale)))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

        canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)
        offset_x = (target_width - new_w) // 2
        offset_y = (target_height - new_h) // 2
        canvas[offset_y : offset_y + new_h, offset_x : offset_x + new_w] = resized
        return canvas

    def _annotate(self, frame: np.ndarray, tracks: list[Track]) -> np.ndarray:
        """Draw every box, centre dot and ID label onto a frame."""
        for track in tracks:
            p1 = (int(track.x1), int(track.y1))
            p2 = (int(track.x2), int(track.y2))
            cv2.rectangle(frame, p1, p2, BOX_COLOR_BGR, 2)
            centre = (int(track.cx), int(track.cy))
            # the dot is the exact pixel the zone test uses
            cv2.circle(frame, centre, 3, BOX_COLOR_BGR, -1)

            label = f"ID {track.id}"
            (text_w, text_h), _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1
            )
            cv2.rectangle(
                frame,
                (p1[0], max(0, p1[1] - text_h - 6)),
                (p1[0] + text_w + 8, p1[1]),
                (7, 12, 18),
                -1,
            )
            cv2.putText(
                frame,
                label,
                (p1[0] + 4, max(text_h, p1[1] - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                TEXT_COLOR,
                1,
                cv2.LINE_AA,
            )
        return frame

    def _save_snapshot(self, frame: np.ndarray, event: Event, now: float) -> str:
        """Write the annotated frame as evidence and return its filename."""
        safe_zone = "".join(
            character if character.isalnum() else "_" for character in event.zone
        )
        filename = f"{int(now * 1000)}_{safe_zone}_{event.type}.jpg"
        cv2.imwrite(
            str(self.snapshot_dir / filename),
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), 82],
        )
        return filename

    def _update_heatmap(
        self, tracks: list[Track], frame_w: int, frame_h: int, now: float
    ) -> list[float]:
        """Accumulate presence, decay it, and return normalised cell values."""
        elapsed = 0.0 if self._heat_at is None else max(0.0, now - self._heat_at)
        self._heat_at = now
        self._heat *= math.exp(-elapsed / HEAT_TAU_SECONDS)
        for track in tracks:
            col = min(HEAT_COLS - 1, max(0, int(track.cx / max(1, frame_w) * HEAT_COLS)))
            row = min(HEAT_ROWS - 1, max(0, int(track.cy / max(1, frame_h) * HEAT_ROWS)))
            self._heat[row, col] += 1.0

        peak = float(self._heat.max())
        if peak <= 0:
            return [0.0] * (HEAT_COLS * HEAT_ROWS)
        return np.round(self._heat / peak, 3).flatten().tolist()

    def _build_stats(
        self,
        zone_stats: dict,
        now: float,
        frame_w: int,
        frame_h: int,
        fps: float,
        heat_values: list[float],
    ) -> dict:
        """Assemble the JSON payload /api/stats serves."""
        warning_ratio = float(self.config["rules"].get("warning_ratio", 0.80))
        zones = []
        for zone_config in self.config["zones"]:
            name = zone_config["name"]
            stats = zone_stats.get(name)
            count = stats.count if stats else 0
            capacity = max(1, int(stats.capacity if stats else zone_config.get("capacity", 10)))
            ratio = count / capacity
            status = "critical" if ratio >= 1.0 else "warning" if ratio >= warning_ratio else "ok"
            zones.append(
                {
                    "name": name,
                    "count": count,
                    "capacity": capacity,
                    "density": stats.density if stats else 0.0,
                    "max_dwell": stats.max_dwell if stats else 0.0,
                    "velocity": stats.velocity if stats else 0.0,
                    "status": status,
                    "polygon": zone_config["polygon"],
                }
            )

        return {
            "ts": now,
            "source": Path(self.source).name,
            "camera": self.config.get("camera", {}).get("name", "Camera 01"),
            "status": "live",
            "fps": round(fps, 1),
            "frame": {"w": frame_w, "h": frame_h},
            "occupancy": sum(zone["count"] for zone in zones),
            "zones": zones,
            "heatmap": {
                "cols": HEAT_COLS,
                "rows": HEAT_ROWS,
                "values": heat_values,
            },
            "events_total": self._events_total,
        }

    def _run(self) -> None:
        """The loop. Runs on the worker thread until stop() is called."""
        capture = cv2.VideoCapture(self.source)
        if not capture.isOpened():
            self._set_status("offline")
            return

        target_width = int(self.config["video"].get("width", 1280))
        jpeg_quality = int(self.config["video"].get("jpeg_quality", 82))
        target_fps = float(self.config["video"].get("target_fps", 12))
        frame_interval = 1.0 / max(1.0, target_fps)

        # skip frames so playback stays near real time, not slow motion
        source_fps = float(capture.get(cv2.CAP_PROP_FPS) or target_fps)
        frame_step = max(1, int(round(source_fps / max(1.0, target_fps))))

        last_frame_at = 0.0

        while not self._stop.is_set():
            loop_started = time.time()
            # grab() advances without decoding, so skipping is cheap
            for _ in range(frame_step - 1):
                if not capture.grab():
                    break
            ok, frame = capture.read()
            if not ok:
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = capture.read()
                if not ok:
                    self._set_status("ended")
                    time.sleep(0.5)
                    continue
                # new pass through the clip: clear IDs, dwell timers and speeds
                self.detector.reset()
                self.zone_engine.reset()

            frame = self._resize(frame, target_width)
            frame_h, frame_w = frame.shape[:2]

            tracks = self.detector.detect(frame)
            # wall clock; dwell and cooldown are real seconds
            now = time.time()
            zone_stats = self.zone_engine.update(tracks, now, frame_w, frame_h)
            events = self.rule_engine.evaluate(zone_stats, now)

            # draw on a copy so the clean frame survives
            annotated = self._annotate(frame.copy(), tracks)
            for event in events:
                # evidence first, then the row that points at it
                snapshot = self._save_snapshot(annotated, event, now)
                self.store.add(event, snapshot)
            self._add_events(len(events))

            heat_values = self._update_heatmap(tracks, frame_w, frame_h, now)

            # encode once per frame, not once per viewer
            ok, buffer = cv2.imencode(
                ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
            )
            if ok:
                # measured rate, smoothed so the KPI does not jitter
                instant_fps = 1.0 / max(1e-6, now - last_frame_at) if last_frame_at else 0.0
                self._fps = (
                    instant_fps
                    if self._fps == 0.0
                    else (0.85 * self._fps + 0.15 * instant_fps)
                )
                last_frame_at = now
                stats = self._build_stats(
                    zone_stats, now, frame_w, frame_h, self._fps, heat_values
                )
                self._set_state(buffer.tobytes(), stats)

            elapsed = time.time() - loop_started
            # pace to target_fps; if we overran, no sleep
            time.sleep(max(0.0, frame_interval - elapsed))

        capture.release()
        self._set_status("stopped")
