# Clip credits and licences

These clips are bundled only so the dashboard can be demonstrated offline. They are
not redistributed for commercial use.

## `demo-crowd.webm` — crowd scenario

- **Source:** `MOT17-04-SDP-raw.webm` from the
  [MOTChallenge MOT17 data page](https://motchallenge.net/data/MOT17/)
  (`https://motchallenge.net/sequenceVideos/MOT17-04-SDP-raw.webm`).
- **Sequence:** MOT17-04-SDP — "Pedestrian street at night, elevated viewpoint",
  960 × 540, 30 fps, 1,050 frames (35 s).
- **Licence:** [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/).
  The MOTChallenge site states that the sequences are released under
  CC BY-NC-SA 3.0. **Non-commercial use only.**
- **Changes:** none to the pixels. The file was downloaded as-is and copied to
  `demo-crowd.webm`; the dashboard letterboxes it to 1280 × 720 and annotates it
  at runtime.
- **Attribution:** MOTChallenge / MOT17 benchmark, used for a university
  coursework demonstration.

## `normal-corridor.avi` — baseline / false-positive scenario

- **Source:** `vtest.avi` from the OpenCV samples data set
  ([opencv/opencv, `samples/data/vtest.avi`](https://github.com/opencv/opencv/blob/4.x/samples/data/vtest.avi)).
  It is the classic pedestrian-detection sample video shipped with OpenCV.
- **Sequence:** 768 × 576, 10 fps, 795 frames (79.5 s), mostly empty corridor
  with a few pedestrians.
- **Licence:** the OpenCV repository is Apache-2.0. The sample clip's original
  provenance is not separately documented by OpenCV, so it is included here as
  demo media only.
- **Changes:** renamed from `vtest.avi` to `normal-corridor.avi`. No pixel
  changes.

## What is *not* bundled

`data/clips/raw/` and `people-detection.mp4` were investigation material and are
not part of the submission. `data/snapshots/*.jpg` are generated at runtime from
the clips above.
