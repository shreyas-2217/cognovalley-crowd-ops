"""Thin wrapper around Ultralytics YOLO + ByteTrack."""

from __future__ import annotations

from app.zones import Track


class PersonDetector:
    """Owns the YOLO model and turns one video frame into a list of Track."""

    def __init__(
        self,
        model_path: str = "yolo11n.pt",
        conf: float = 0.40,
        iou: float = 0.50,
        min_box_height: float = 36.0,
    ) -> None:
        # imported here so tests never pay torch's multi-second import
        from ultralytics import YOLO

        # load the weights once at startup
        self.model = YOLO(model_path)
        self.conf = float(conf)
        self.iou = float(iou)
        self.min_box_height = float(min_box_height)

    def reset(self) -> None:
        """Drop tracker state so a looped clip starts with clean IDs."""
        # clears ByteTrack's memory so a looped clip starts with fresh IDs
        self.model.predictor = None

    def detect(self, frame, persist: bool = True) -> list[Track]:
        """Return tracked people in one frame."""
        # detect + associate in one call; `track` is what assigns persistent IDs
        results = self.model.track(
            frame,
            persist=persist,
            # COCO class 0 = person; the model cannot report cars or dogs
            classes=[0],
            conf=self.conf,
            iou=self.iou,
            tracker="bytetrack.yaml",
            verbose=False,
        )
        if not results:
            return []

        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []

        ids = boxes.id
        tracks: list[Track] = []

        for index, box in enumerate(boxes):
            x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
            # false-positive filter: drop distant or noisy boxes
            if (y2 - y1) < self.min_box_height:
                continue

            # -1 means untracked; the zone engine discards those
            track_id = int(ids[index].item()) if ids is not None else -1
            tracks.append(Track(track_id, x1, y1, x2, y2))

        return tracks
