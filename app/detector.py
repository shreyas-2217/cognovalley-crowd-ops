"""Thin wrapper around Ultralytics YOLO + ByteTrack.

The rest of the app only sees a list of Track objects, so the model can
be swapped without touching the zone logic.
"""

# TEACHING NOTE: makes type hints lazily evaluated so we can write
# list[Track] even on older Python versions.
from __future__ import annotations

# The only thing this module needs from our own code: the Track container class.
# ZoneEngine and everything else never see a YOLO object, only Track objects.
from app.zones import Track


class PersonDetector:
    """Owns the YOLO model and turns one video frame into a list of Track.

    This is the ONLY place in the whole project where machine learning runs.
    Everything downstream (zones, rules) is plain arithmetic.
    """

    def __init__(
        self,
        model_path: str = "yolo11n.pt",   # the weight file; "n" = nano, smallest YOLO11
        conf: float = 0.40,              # minimum confidence for a detection to count (0..1)
        iou: float = 0.50,               # NMS overlap threshold; higher = keeps more overlapping boxes
        min_box_height: float = 36.0,    # ignore boxes shorter than this many pixels (filters distant people)
    ) -> None:
        # Imported inside the method on purpose: importing ultralytics pulls in
        # torch, which is slow (~seconds). Tests that never build a detector
        # therefore never pay that cost.
        from ultralytics import YOLO

        self.model = YOLO(model_path)      # loads the weights into memory once
        self.conf = float(conf)            # stored so detect() can pass it each frame
        self.iou = float(iou)              # same
        self.min_box_height = float(min_box_height)  # used by our own filtering below

    def reset(self) -> None:
        """Drop tracker state so a looped clip starts with clean IDs."""
        # ByteTrack keeps internal memory of previously seen tracks. Setting the
        # predictor to None makes Ultralytics rebuild it on the next call, which
        # clears that memory. Without this, IDs from the end of one pass would
        # continue into the start of the next pass and corrupt dwell timers.
        self.model.predictor = None

    def detect(self, frame, persist: bool = True) -> list[Track]:
        """Return tracked people in one frame."""
        # model.track() = detect + associate. It returns a Results object per frame.
        # `track` (not `predict`) is what assigns a persistent integer ID to a person.
        results = self.model.track(
            frame,                      # one BGR image (numpy array) from OpenCV
            persist=persist,            # True = remember tracks between calls, so IDs stay stable
            classes=[0],  # COCO class 0 = person — the model cannot report cars/dogs
            conf=self.conf,             # drop detections below this confidence
            iou=self.iou,               # NMS threshold for overlapping boxes
            tracker="bytetrack.yaml",   # association algorithm shipped with Ultralytics
            verbose=False,              # no per-frame console spam
        )
        # If the model returned nothing at all, there is nobody in this frame.
        if not results:
            return []

        # results is a list with one entry per input image; we passed one frame.
        # .boxes holds all detections for that frame.
        boxes = results[0].boxes
        # Defensive: no boxes, or an empty box list -> nobody.
        if boxes is None or len(boxes) == 0:
            return []

        # `ids` is a tensor of tracker IDs, one per box, aligned by index.
        # It can be None if tracking is disabled, so we handle that below.
        ids = boxes.id
        tracks: list[Track] = []        # what we will return to the caller

        # enumerate gives us `index` so we can read the matching ID from `ids`.
        for index, box in enumerate(boxes):
            # box.xyxy[0] = [x1, y1, x2, y2] in pixels, top-left and bottom-right.
            # .tolist() makes it a plain Python list; float() converts tensor numbers.
            x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
            # Height of the box = vertical size on screen. Small boxes are distant
            # people (or noise), so we skip them. This is our main false-positive control.
            if (y2 - y1) < self.min_box_height:
                continue

            # .item() pulls the single number out of a 1-element tensor.
            # If tracking gave no ID, use -1; ZoneEngine filters id >= 0 out.
            track_id = int(ids[index].item()) if ids is not None else -1
            # Store the box as a lightweight Track. From here on, no model needed.
            tracks.append(Track(track_id, x1, y1, x2, y2))

        return tracks
