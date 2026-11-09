"""Flask application: MJPEG stream, JSON APIs, and the dashboard.

Run with:  python -m app.main
"""

# TEACHING NOTE: defers evaluation of type hints.
from __future__ import annotations

import argparse     # command-line flags (--config, --source, --port, --reset)
import atexit       # run a cleanup function when the process exits
import time         # time.sleep in the MJPEG generator

# Flask supplies routing, JSON helpers, the streaming Response and safe file serving.
from flask import Flask, Response, jsonify, request, send_from_directory

from app.camera_worker import CameraWorker
from app.config import BASE_DIR, load_config, resolve_path
from app.detector import PersonDetector
from app.rules import RuleEngine
from app.storage import EventStore
from app.zones import ZoneEngine


def purge_snapshots(config: dict) -> None:
    """Remove stored evidence images so a cleared log leaves no orphans."""
    directory = resolve_path(config["storage"]["snapshot_dir"])
    # Nothing to do if the folder was never created.
    if not directory.is_dir():
        return
    for path in directory.glob("*.jpg"):
        try:
            path.unlink()
        except OSError:
            # A file could be locked by a viewer; skipping it is better than
            # crashing the whole Clear operation.
            pass


def create_app(
    config: dict | None = None,
    start_worker: bool = True,
    reset_events: bool = False,
) -> Flask:
    # Fall back to the default config when none is supplied (useful in tests).
    config = config or load_config()

    # --- build the object graph once, at startup ---
    store = EventStore(resolve_path(config["storage"]["db_path"]))
    if reset_events:
        # --reset means "start from a clean slate": wipe rows and evidence.
        store.clear()
        purge_snapshots(config)
    detector = PersonDetector(
        model_path=config["detector"]["model"],
        conf=config["detector"]["conf"],
        iou=config["detector"]["iou"],
        min_box_height=config["detector"]["min_box_height"],
    )
    zone_engine = ZoneEngine(
        config["zones"],
        # .get with a default keeps a config without this key working.
        pixels_per_meter=config["video"].get("pixels_per_meter", 45),
    )
    rule_engine = RuleEngine(config["rules"])
    worker = CameraWorker(config, detector, zone_engine, rule_engine, store)

    app = Flask(
        __name__,
        # Serve our own static folder instead of Flask's default.
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
    )
    # Stash the long-lived objects on the app so routes can reach them.
    app.config["WORKER"] = worker
    app.config["STORE"] = store
    app.config["APP_CONFIG"] = config

    if start_worker:
        # Tests pass start_worker=False so no thread or model is created.
        worker.start()
        # Make sure the thread is stopped when the process exits normally.
        atexit.register(worker.stop)

    @app.get("/")
    def index():
        # send_from_directory refuses paths outside the folder, unlike a raw join.
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/video")
    def video():
        def generate():
            """Yield one multipart JPEG each time the worker publishes a new frame."""
            last_id = -1     # the frame counter we last sent
            while True:
                # Atomic read of (id, jpeg) under the worker's lock.
                frame_id, frame = worker.get_frame_with_id()
                if frame is None or frame_id == last_id:
                    # Nothing new yet: wait briefly instead of busy-spinning.
                    time.sleep(0.02)
                    continue
                last_id = frame_id
                # MJPEG framing: a boundary line, headers, then the JPEG bytes.
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )

        # multipart/x-mixed-replace is the convention browsers understand for MJPEG:
        # each new part replaces the previous one in the <img> tag.
        response = Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")
        # Stop any proxy or browser cache from holding an old stream.
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/stats")
    def api_stats():
        # get_stats() returns a deep copy, so jsonify cannot mutate worker state.
        response = jsonify(worker.get_stats())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/events")
    def api_events():
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            # A non-numeric ?limit= must not 500 the dashboard.
            limit = 50
        # Clamp so a client cannot ask for millions of rows.
        limit = max(1, min(200, limit))
        response = jsonify(store.recent(limit))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/events/clear")
    def api_events_clear():
        # All three steps are needed or the UI shows stale evidence:
        store.clear()                  # 1. delete database rows
        purge_snapshots(config)        # 2. delete evidence JPEGs
        worker.reset_events_total()    # 3. zero the session counter
        return jsonify({"ok": True})

    @app.get("/api/health")
    def api_health():
        stats = worker.get_stats()
        # A small payload for quick liveness checks; not used by the dashboard.
        return jsonify(
            {
                "status": stats.get("status", "unknown"),
                "fps": stats.get("fps", 0),
                "source": stats.get("source", ""),
                "events_total": stats.get("events_total", 0),
            }
        )

    @app.get("/api/config")
    def api_config():
        # Lets a client inspect the active zones/rules without reading JSON files.
        return jsonify(
            {
                "zones": config["zones"],
                "rules": config["rules"],
                "video": config["video"],
            }
        )

    @app.get("/snapshots/<path:filename>")
    def snapshot(filename: str):
        # send_from_directory rejects ../ traversal, so evidence files are safe.
        return send_from_directory(
            resolve_path(config["storage"]["snapshot_dir"]), filename
        )

    return app


def main() -> None:
    """Parse CLI flags and run the development server."""
    parser = argparse.ArgumentParser(description="CognoValley Track A dashboard")
    parser.add_argument(
        "--config",
        default="configs/demo.json",
        help="JSON config override (defaults to configs/demo.json)",
    )
    parser.add_argument("--source", default=None, help="Video file to analyse")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete stored events and snapshots before starting",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.source:
        # --source wins over the config file, so any clip can be analysed
        # without editing JSON.
        config["source"] = args.source

    app = create_app(config, reset_events=args.reset)
    app.run(
        host="127.0.0.1",      # local only; nothing is exposed to the network
        port=args.port,
        threaded=True,         # /video streams forever, so other routes need their own thread
        debug=False,
        use_reloader=False,    # the reloader forks a second process -> two models, twice the CPU
    )


if __name__ == "__main__":
    main()
