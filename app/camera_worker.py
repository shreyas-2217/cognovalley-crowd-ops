"""Background inference worker.

One thread owns the video source and the model. Flask only reads the
latest JPEG and the latest stats snapshot, so requests never block on
inference.
"""

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
# Seconds for a cell to fade to 1/e of its weight. Short enough that the map
# follows the people currently on screen instead of turning into a full-frame wash.
HEAT_TAU_SECONDS = 4.0
BOX_COLOR_BGR = (238, 211, 34)  # cyan
TEXT_COLOR = (240, 248, 255)


class CameraWorker:
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

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._jpeg: bytes | None = None
        self._frame_id = 0
        self._fps = 0.0
        self._events_total = store.count()
        self._heat = np.zeros((HEAT_ROWS, HEAT_COLS), dtype=np.float32)
        self._heat_at: float | None = None
        self._stats = self._empty_stats("starting")

    def _empty_stats(self, status: str) -> dict:
        zones = [
            {
                "name": zone["name"],
                "count": 0,
                "capacity": int(zone.get("capacity", 10)),
                "density": 0.0,
                "avg_dwell": 0.0,
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
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def get_frame(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def get_frame_with_id(self) -> tuple[int, bytes | None]:
        with self._lock:
            return self._frame_id, self._jpeg

    def get_stats(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._stats)

    def reset_events_total(self) -> None:
        with self._lock:
            self._events_total = 0
            self._stats = copy.deepcopy(self._stats)
            self._stats["events_total"] = 0

    def _set_state(self, jpeg: bytes, stats: dict) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._frame_id += 1
            self._stats = stats

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._stats = copy.deepcopy(self._stats)
            self._stats["status"] = status

    def _resize(self, frame: np.ndarray, target_width: int) -> np.ndarray:
        """Letterbox to 16:9 so browser overlays always line up with the frame."""
        height, width = frame.shape[:2]
        target_height = int(round(target_width * 9 / 16))
        if width == 0 or height == 0:
            return frame

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
        for track in tracks:
            p1 = (int(track.x1), int(track.y1))
            p2 = (int(track.x2), int(track.y2))
            cv2.rectangle(frame, p1, p2, BOX_COLOR_BGR, 2)
            centre = (int(track.cx), int(track.cy))
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
        # Time-based decay keeps the trail the same length at any frame rate:
        # cells hold a person's recent positions, then fade over a few seconds.
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
                    "avg_dwell": stats.avg_dwell if stats else 0.0,
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
        capture = cv2.VideoCapture(self.source)
        if not capture.isOpened():
            self._set_status("offline")
            return

        target_width = int(self.config["video"].get("width", 1280))
        jpeg_quality = int(self.config["video"].get("jpeg_quality", 82))
        target_fps = float(self.config["video"].get("target_fps", 12))
        frame_interval = 1.0 / max(1.0, target_fps)

        # Keep playback close to real time: a 30 fps clip analysed at 10 fps
        # should skip two frames, not play in slow motion.
        source_fps = float(capture.get(cv2.CAP_PROP_FPS) or target_fps)
        frame_step = max(1, int(round(source_fps / max(1.0, target_fps))))

        last_frame_at = 0.0

        while not self._stop.is_set():
            loop_started = time.time()
            for _ in range(frame_step - 1):
                if not capture.grab():
                    break
            ok, frame = capture.read()
            if not ok:
                # Loop the clip so a live demo never ends on a freeze frame.
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = capture.read()
                if not ok:
                    self._set_status("ended")
                    time.sleep(0.5)
                    continue
                # A fresh pass through the clip is a fresh scene: clear IDs,
                # dwell timers, and speed history so metrics do not leak.
                self.detector.reset()
                self.zone_engine.reset()

            frame = self._resize(frame, target_width)
            frame_h, frame_w = frame.shape[:2]

            tracks = self.detector.detect(frame)
            now = time.time()
            zone_stats = self.zone_engine.update(tracks, now, frame_w, frame_h)
            events = self.rule_engine.evaluate(zone_stats, now)

            annotated = self._annotate(frame.copy(), tracks)
            for event in events:
                snapshot = self._save_snapshot(annotated, event, now)
                self.store.add(event, snapshot)
                self._events_total += 1

            heat_values = self._update_heatmap(tracks, frame_w, frame_h, now)

            ok, buffer = cv2.imencode(
                ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
            )
            if ok:
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
            time.sleep(max(0.0, frame_interval - elapsed))

        capture.release()
        self._set_status("stopped")
