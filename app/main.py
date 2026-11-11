"""Flask application: MJPEG stream, JSON APIs, and the dashboard."""

from __future__ import annotations

import argparse
import atexit
import time

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
    if not directory.is_dir():
        return
    for path in directory.glob("*.jpg"):
        try:
            path.unlink()
        except OSError:
            pass


def create_app(
    config: dict | None = None,
    start_worker: bool = True,
    reset_events: bool = False,
) -> Flask:
    config = config or load_config()

    # build the object graph once, at startup
    store = EventStore(resolve_path(config["storage"]["db_path"]))
    if reset_events:
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
        pixels_per_meter=config["video"].get("pixels_per_meter", 45),
    )
    rule_engine = RuleEngine(config["rules"])
    worker = CameraWorker(config, detector, zone_engine, rule_engine, store)

    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
    )
    app.config["WORKER"] = worker
    app.config["STORE"] = store
    app.config["APP_CONFIG"] = config

    if start_worker:
        worker.start()
        # make sure the thread stops when the process exits
        atexit.register(worker.stop)

    @app.get("/")
    def index():
        # send_from_directory refuses paths outside the folder
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/video")
    def video():
        def generate():
            """Yield one multipart JPEG each time the worker publishes a new frame."""
            last_id = -1
            while True:
                # atomic read of (frame id, JPEG) under the worker's lock
                frame_id, frame = worker.get_frame_with_id()
                # nothing new yet: wait instead of busy-spinning
                if frame is None or frame_id == last_id:
                    time.sleep(0.02)
                    continue
                last_id = frame_id
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )

        # MJPEG: each part replaces the previous one in the <img>
        response = Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/stats")
    def api_stats():
        response = jsonify(worker.get_stats())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/events")
    def api_events():
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        # clamp so a client cannot ask for millions of rows
        limit = max(1, min(200, limit))
        response = jsonify(store.recent(limit))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/events/clear")
    # all three steps are needed, or the UI shows stale evidence
    def api_events_clear():
        store.clear()
        purge_snapshots(config)
        worker.reset_events_total()
        return jsonify({"ok": True})

    @app.get("/api/health")
    def api_health():
        stats = worker.get_stats()
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
        return jsonify(
            {
                "zones": config["zones"],
                "rules": config["rules"],
                "video": config["video"],
            }
        )

    @app.get("/snapshots/<path:filename>")
    def snapshot(filename: str):
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
        config["source"] = args.source

    app = create_app(config, reset_events=args.reset)
    app.run(
        host="127.0.0.1",
        port=args.port,
        # /video streams forever, so other routes need their own thread
        threaded=True,
        debug=False,
        # the reloader forks a second process and loads two models
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
