"""Thin wrapper around Ultralytics YOLO + ByteTrack.

The rest of the app only sees a list of Track objects, so the model can
be swapped without touching the zone logic.
"""

from __future__ import annotations

from app.zones import Track


class PersonDetector:
    def __init__(
        self,
        model_path: str = "yolo11n.pt",
        conf: float = 0.40,
        iou: float = 0.50,
        min_box_height: float = 36.0,
    ) -> None:
        from ultralytics import YOLO

        self.model = YOLO(model_path)
        self.conf = float(conf)
        self.iou = float(iou)
        self.min_box_height = float(min_box_height)

    def reset(self) -> None:
        """Drop tracker state so a looped clip starts with clean IDs."""
        self.model.predictor = None

    def detect(self, frame, persist: bool = True) -> list[Track]:
        """Return tracked people in one frame."""
        results = self.model.track(
            frame,
            persist=persist,
            classes=[0],  # COCO class 0 = person
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
            if (y2 - y1) < self.min_box_height:
                continue

            track_id = int(ids[index].item()) if ids is not None else -1
            confidence = float(box.conf[0].item())
            tracks.append(Track(track_id, x1, y1, x2, y2, confidence))

        return tracks
