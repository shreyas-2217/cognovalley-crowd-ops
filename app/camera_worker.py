"""Background inference worker.

One thread owns the video source and the model. Flask only reads the
latest JPEG and the latest stats snapshot, so requests never block on
inference.
"""

# TEACHING NOTE: defers evaluation of type hints.
from __future__ import annotations

import copy         # deepcopy the stats dict before handing it out
import math         # math.exp for heat decay
import threading    # Thread, Lock, Event
import time         # timestamps, sleep, FPS measurement
from pathlib import Path    # deriving the source filename for display

import cv2          # VideoCapture, resize, drawing, JPEG encoding
import numpy as np  # the heat map grid

from app.config import resolve_path
from app.detector import PersonDetector
from app.rules import Event, RuleEngine
from app.storage import EventStore
from app.zones import Track, ZoneEngine

# Heat map resolution. 32 x 18 has the same 16:9 shape as the video, so each
# cell maps to an equal slice of the frame.
HEAT_COLS = 32
HEAT_ROWS = 18
# Seconds for a cell to fade to 1/e of its weight. Short enough that the map
# follows the people currently on screen instead of turning into a full-frame wash.
HEAT_TAU_SECONDS = 4.0
# BGR order in OpenCV. This is (34, 211, 238) in RGB — the dashboard's cyan.
BOX_COLOR_BGR = (238, 211, 34)  # cyan
TEXT_COLOR = (240, 248, 255)    # near-white label text


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
        # Everything is injected rather than constructed here, so tests can
        # substitute fakes and main.py owns the assembly.
        self.config = config
        self.detector = detector
        self.zone_engine = zone_engine
        self.rule_engine = rule_engine
        self.store = store

        # Full path to the video file; relative config paths resolve to the root.
        self.source = str(resolve_path(config["source"]))
        self.snapshot_dir = resolve_path(config["storage"]["snapshot_dir"])
        # parents=True creates intermediate dirs; exist_ok avoids an error if present.
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

        # ONE lock protecting the published state below. Never held during
        # inference — that is what keeps HTTP requests responsive.
        self._lock = threading.Lock()
        # Set by stop(); the loop checks it every iteration.
        self._stop = threading.Event()
        # Handle to the running thread, or None before start().
        self._thread: threading.Thread | None = None

        # ---- published state, always read/written under _lock ----
        self._jpeg: bytes | None = None     # latest encoded frame
        self._frame_id = 0                  # increments per published frame
        self._fps = 0.0                     # smoothed processing rate
        # Seed from the database so a restart shows the real stored count.
        self._events_total = store.count()
        # 2-D float grid of recent presence, rows x cols.
        self._heat = np.zeros((HEAT_ROWS, HEAT_COLS), dtype=np.float32)
        # When the heat grid was last decayed; None until the first frame.
        self._heat_at: float | None = None
        # What /api/stats serves before the first frame is processed.
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
                "polygon": zone["polygon"],   # normalised points, for the SVG overlay
            }
            for zone in self.config["zones"]
        ]
        return {
            "ts": time.time(),
            # Path(...).name strips directories: "demo-crowd.webm" for display.
            "source": Path(self.source).name,
            "camera": self.config.get("camera", {}).get("name", "Camera 01"),
            "status": status,       # "starting" until the first frame
            "fps": 0.0,
            "frame": {"w": 0, "h": 0},   # zero size tells the UI "not live yet"
            "occupancy": 0,
            "zones": zones,
            # The heat grid flattened row-major: 576 values for UI convenience.
            "heatmap": {
                "cols": HEAT_COLS,
                "rows": HEAT_ROWS,
                "values": [0.0] * (HEAT_COLS * HEAT_ROWS),
            },
            "events_total": self._events_total,
        }

    def start(self) -> None:
        """Start the background loop if it is not already running."""
        # Idempotent: calling start twice must not create two loops.
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera-worker", daemon=True)
        # daemon=True means the thread dies with the process, so a hung loop
        # cannot keep Python alive after Ctrl+C.
        self._thread.start()

    def stop(self) -> None:
        """Ask the loop to finish and wait briefly for it."""
        self._stop.set()
        if self._thread is not None:
            # join() blocks up to 3 s; a stuck capture cannot hang shutdown.
            self._thread.join(timeout=3.0)

    def get_frame_with_id(self) -> tuple[int, bytes | None]:
        """Return (frame counter, latest JPEG) atomically."""
        with self._lock:
            # Returning the counter lets /video detect "nothing new".
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
            # Done here, not inline in the loop, so a Clear cannot land between
            # an event being stored and being counted.
            self._events_total += count

    def _set_state(self, jpeg: bytes, stats: dict) -> None:
        """Publish a new frame and its stats atomically."""
        with self._lock:
            self._jpeg = jpeg
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
        # 16:9 height derived from the requested width.
        target_height = int(round(target_width * 9 / 16))
        if width == 0 or height == 0:
            # Degenerate input; nothing sensible to scale.
            return frame

        # Scale that fits the frame INSIDE the target box without distorting it.
        scale = min(target_width / width, target_height / height)
        new_w = max(1, int(round(width * scale)))
        new_h = max(1, int(round(height * scale)))
        # INTER_AREA is the good choice for shrinking; avoids aliasing.
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # Black canvas of exactly the target size, 3 channels.
        canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)
        # Centre the resized image; the leftover strips become letterbox bars.
        offset_x = (target_width - new_w) // 2
        offset_y = (target_height - new_h) // 2
        canvas[offset_y : offset_y + new_h, offset_x : offset_x + new_w] = resized
        return canvas

    def _annotate(self, frame: np.ndarray, tracks: list[Track]) -> np.ndarray:
        """Draw every box, centre dot and ID label onto a frame."""
        for track in tracks:
            # OpenCV needs integer pixel tuples.
            p1 = (int(track.x1), int(track.y1))
            p2 = (int(track.x2), int(track.y2))
            cv2.rectangle(frame, p1, p2, BOX_COLOR_BGR, 2)
            centre = (int(track.cx), int(track.cy))
            # Filled dot at the box centre — the exact point the zones test.
            cv2.circle(frame, centre, 3, BOX_COLOR_BGR, -1)

            label = f"ID {track.id}"
            # Measure the text so the background plate fits it exactly.
            (text_w, text_h), _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1
            )
            # Dark plate behind the label, lifted above the box.
            # max(0, ...) stops it drawing off the top edge.
            cv2.rectangle(
                frame,
                (p1[0], max(0, p1[1] - text_h - 6)),
                (p1[0] + text_w + 8, p1[1]),
                (7, 12, 18),
                -1,     # thickness -1 = filled
            )
            cv2.putText(
                frame,
                label,
                (p1[0] + 4, max(text_h, p1[1] - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                TEXT_COLOR,
                1,
                cv2.LINE_AA,    # anti-aliased, so text is readable at 1280px
            )
        return frame

    def _save_snapshot(self, frame: np.ndarray, event: Event, now: float) -> str:
        """Write the annotated frame as evidence and return its filename."""
        # Zone names contain spaces/dots; keep only alphanumerics for the filename.
        safe_zone = "".join(
            character if character.isalnum() else "_" for character in event.zone
        )
        # Milliseconds prefix keeps files unique and sorts chronologically.
        filename = f"{int(now * 1000)}_{safe_zone}_{event.type}.jpg"
        cv2.imwrite(
            str(self.snapshot_dir / filename),
            frame,
            # Quality 82 keeps evidence images small without visible loss.
            [int(cv2.IMWRITE_JPEG_QUALITY), 82],
        )
        return filename

    def _update_heatmap(
        self, tracks: list[Track], frame_w: int, frame_h: int, now: float
    ) -> list[float]:
        """Accumulate presence, decay it, and return normalised cell values."""
        # Time-based decay keeps the trail the same length at any frame rate:
        # cells hold a person's recent positions, then fade over a few seconds.
        elapsed = 0.0 if self._heat_at is None else max(0.0, now - self._heat_at)
        self._heat_at = now
        # Multiply every cell by e^(-dt/tau). dt=4s -> ~37% remains.
        self._heat *= math.exp(-elapsed / HEAT_TAU_SECONDS)
        for track in tracks:
            # Map pixel centre -> grid cell, clamped to the valid range so a
            # box exactly on the right/bottom edge cannot index out of bounds.
            col = min(HEAT_COLS - 1, max(0, int(track.cx / max(1, frame_w) * HEAT_COLS)))
            row = min(HEAT_ROWS - 1, max(0, int(track.cy / max(1, frame_h) * HEAT_ROWS)))
            # One unit of "presence" at that person's location.
            self._heat[row, col] += 1.0

        peak = float(self._heat.max())
        if peak <= 0:
            # Nobody has ever been seen: return an all-zero grid.
            return [0.0] * (HEAT_COLS * HEAT_ROWS)
        # Normalise to 0..1 by the hottest cell, so the UI gradient means the
        # same thing regardless of how many people are on screen.
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
        # Read once outside the loop instead of per zone.
        warning_ratio = float(self.config["rules"].get("warning_ratio", 0.80))
        zones = []
        for zone_config in self.config["zones"]:
            name = zone_config["name"]
            stats = zone_stats.get(name)
            # Fall back to zero if the engine did not return this zone.
            count = stats.count if stats else 0
            capacity = max(1, int(stats.capacity if stats else zone_config.get("capacity", 10)))
            ratio = count / capacity
            # Display status. Same thresholds as RuleEngine, but computed on the
            # CURRENT frame with no duration gate — it colours the UI, it is not
            # the alerting logic.
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
                    # Normalised polygon, so the browser can draw the overlay.
                    "polygon": zone_config["polygon"],
                }
            )

        return {
            "ts": now,
            "source": Path(self.source).name,
            "camera": self.config.get("camera", {}).get("name", "Camera 01"),
            "status": "live",       # we are inside the loop, so we are live
            "fps": round(fps, 1),
            "frame": {"w": frame_w, "h": frame_h},
            # Total people across all zones. Zones do not overlap in the demo,
            # so this is not double-counting.
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
            # Unreadable file: report offline and leave the thread.
            self._set_status("offline")
            return

        # Read output settings once; they never change during a run.
        target_width = int(self.config["video"].get("width", 1280))
        jpeg_quality = int(self.config["video"].get("jpeg_quality", 82))
        target_fps = float(self.config["video"].get("target_fps", 12))
        # Minimum wall-clock time per iteration, used for the sleep at the end.
        frame_interval = 1.0 / max(1.0, target_fps)

        # Keep playback close to real time: a 30 fps clip analysed at 10 fps
        # should skip two frames, not play in slow motion.
        source_fps = float(capture.get(cv2.CAP_PROP_FPS) or target_fps)
        # How many source frames to advance per processed frame. 30/10 = 3.
        frame_step = max(1, int(round(source_fps / max(1.0, target_fps))))

        last_frame_at = 0.0     # timestamp of the previous processed frame

        while not self._stop.is_set():
            loop_started = time.time()
            # grab() decodes nothing, it only advances the read head — so
            # skipping is cheap. read() below decodes the frame we keep.
            for _ in range(frame_step - 1):
                if not capture.grab():
                    break
            ok, frame = capture.read()
            if not ok:
                # Loop the clip so a live demo never ends on a freeze frame.
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = capture.read()
                if not ok:
                    # Still unreadable after rewinding: report it and retry.
                    self._set_status("ended")
                    time.sleep(0.5)
                    continue
                # A fresh pass through the clip is a fresh scene: clear IDs,
                # dwell timers, and speed history so metrics do not leak.
                self.detector.reset()
                self.zone_engine.reset()

            # Letterbox to the fixed output size.
            frame = self._resize(frame, target_width)
            frame_h, frame_w = frame.shape[:2]

            # ---- the whole pipeline, four lines ----
            tracks = self.detector.detect(frame)                    # AI
            now = time.time()                                       # wall clock
            zone_stats = self.zone_engine.update(tracks, now, frame_w, frame_h)  # geometry
            events = self.rule_engine.evaluate(zone_stats, now)     # decisions

            # Annotate a COPY so `frame` stays clean (and copy is cheap here
            # relative to inference).
            annotated = self._annotate(frame.copy(), tracks)
            for event in events:
                # Evidence first, then the row that points at it.
                snapshot = self._save_snapshot(annotated, event, now)
                self.store.add(event, snapshot)
            # Count under the lock, once per frame rather than per event.
            self._add_events(len(events))

            heat_values = self._update_heatmap(tracks, frame_w, frame_h, now)

            # Encode once per frame, not once per viewer, then publish.
            ok, buffer = cv2.imencode(
                ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
            )
            if ok:
                # Instantaneous rate from the gap between processed frames.
                instant_fps = 1.0 / max(1e-6, now - last_frame_at) if last_frame_at else 0.0
                # Exponential smoothing: no jitter in the displayed number.
                # The first frame seeds the value directly to avoid a slow ramp.
                self._fps = (
                    instant_fps
                    if self._fps == 0.0
                    else (0.85 * self._fps + 0.15 * instant_fps)
                )
                last_frame_at = now
                stats = self._build_stats(
                    zone_stats, now, frame_w, frame_h, self._fps, heat_values
                )
                # Single atomic publish: readers see a matching frame and stats.
                self._set_state(buffer.tobytes(), stats)

            # Pace the loop. If processing already took longer than the budget,
            # max(0.0, ...) makes this a no-op and we simply run slower.
            elapsed = time.time() - loop_started
            time.sleep(max(0.0, frame_interval - elapsed))

        # Loop exited via stop(): release the file handle and report it.
        capture.release()
        self._set_status("stopped")
