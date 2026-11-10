# Design decisions

This file records the choices that shaped the project, why they were made, and
what they cost. Each entry is short on purpose: it is the explanation to give if
a reviewer asks "why did you do it this way?".

---

## 1. Flask plus plain HTML/CSS/JavaScript

**Context.** The brief allows any frontend, but the submission must be
explainable line by line by a third-year student who knows only vanilla web
technologies.

**Decision.** Flask serves one HTML page, one stylesheet and two small script
files. There is no React, no Tailwind, no bundler and no build step. Chart.js is
the only third-party browser library and it is vendored into `static/vendor/`.

**Why.** Every line in the UI can be traced in DevTools. There is no toolchain to
break on the presentation laptop, and the page loads from `file`-like
simplicity: `python -m app.main`, then open the browser.

**Cost.** Manual DOM updates instead of declarative rendering. At one update per
second with roughly a dozen elements this is not a problem.

**Rejected.** React/Vite would add a Node toolchain and a build artefact for no
visible gain. Streamlit would hide the UI code, which is exactly what the rubric
asks to see.

---

## 2. YOLO11n with ByteTrack

**Context.** Person detection and tracking must run on a 4-core laptop CPU with
no paid API and no GPU.

**Decision.** Ultralytics YOLO11 nano (`yolo11n.pt`) for detection, ByteTrack for
identity association. Only COCO class 0 (person) is requested.

**Why.** YOLO11n is small (~5 MB), fast enough on CPU, and the Ultralytics API
gives detection and tracking in one call. ByteTrack is built into the same call,
so track IDs are available without a second library.

**Cost.** Ultralytics is **AGPL-3.0**. A public repository that ships the full
source satisfies the sharing obligation for coursework, but AGPL is not suitable
for a closed commercial product. The `PersonDetector` wrapper isolates the model,
so YOLOX (Apache-2.0) is a drop-in fallback if a permissive licence is required.

**Rejected.** A paid cloud vision API (cost, network dependency, privacy). A
COCO-only detector without tracking (no dwell time, no speed, no stable IDs).

---

## 3. One worker thread owns the video and the model

**Context.** Flask handles concurrent requests. YOLO inference takes tens of
milliseconds per frame. Running inference inside a request handler would make
polling unpredictable.

**Decision.** `CameraWorker` runs in one background thread. It decodes, detects,
tracks, measures, applies rules and publishes the newest annotated JPEG plus a
stats snapshot under a lock. Flask handlers only read that published state.
`/video` is an MJPEG generator that waits for a new `_frame_id`.

**Why.** Request latency is decoupled from inference latency. Polling stays cheap
even while a frame is being processed. There is exactly one owner of the model
and the capture object, which avoids OpenCV thread-safety problems.

**Cost.** One extra frame of latency (the browser shows the last completed
frame), and the snapshot must be deep-copied to avoid races. Both are acceptable
for a monitoring dashboard.

**Rejected.** Inference per request (unpredictable, duplicate work). A task queue
such as Celery/Redis (a second service to install for a single camera).

---

## 4. Draw annotations on the server, overlays in the browser

**Context.** Zone polygons must line up with the video at any window size, and
the UI must stay responsive.

**Decision.** Detection boxes, track IDs and the zone tint are drawn into the
JPEG by OpenCV in the worker. Zone outlines, labels, the heat map and all
selection state are drawn by the browser as SVG/canvas layers positioned over
the `<img>`.

**Why.** Boxes belong to the frame and are cheap to draw once. Overlays belong to
the UI: they can be toggled (`#heat`, `#nozones`), highlighted on hover or click,
and redrawn without re-encoding video. The split also keeps the MJPEG stream a
plain image, which every browser supports.

**Cost.** The video is not interactive itself; interaction happens on the overlay
layer. The zone polygons are therefore stored in a normalized coordinate system
(decision 5).

**Rejected.** Drawing every overlay in the browser from raw detection JSON
(more bandwidth, more client work). Drawing everything server-side (no
interaction, heat map tied to frame rate).

---

## 5. Normalized zone polygons plus 16:9 letterboxing

**Context.** The demo clip is 960×540 (16:9), the baseline clip is 768×576 (4:3),
and the browser window can be any size.

**Decision.** Zone polygons are stored as `[x, y]` pairs in 0–1 coordinates.
The worker always letterboxes the source into a 1280×720 (16:9) canvas before
detection and annotation. The browser maps normalized points to the SVG viewBox
`0 0 160 90`.

**Why.** One config works at any output resolution. Because the worker outputs a
single aspect ratio, the SVG, the heat canvas and the JPEG share the same frame
geometry; the 4:3 clip is pillarboxed once, in the worker, and every overlay is
drawn on the same box.

**Cost.** Black bars on 4:3 sources, and zone coordinates must be authored
against the letterboxed frame rather than the original pixels.

**Rejected.** Pixel coordinates in the config (breaks when `video.width`
changes). Per-source aspect handling in the browser (duplicated geometry rules).

---

## 6. SQLite with one table

**Context.** Events must survive a page refresh and be inspectable after the
demo.

**Decision.** One `events` table in SQLite, opened per operation with a lock,
parameterized SQL, no ORM and no migration framework. Each event also writes an
annotated JPEG named `<timestamp>_<zone>_<type>.jpg`.

**Why.** SQLite is in the Python standard library. A single table is enough for
"recent events, newest first". The lock is needed because the worker writes while
Flask reads, but the write rate is a few rows per minute.

**Cost.** No schema migration path and no retention policy; evidence JPEGs
accumulate until **Clear** or `--reset`. For a demo this is honest and simple.

**Rejected.** PostgreSQL/Redis (a service to install). In-memory only (events lost
on refresh, no evidence trail). An ORM (more code to explain, no benefit here).

---

## 7. Duration debounce and per-rule cooldown

**Context.** Raw counts fluctuate every frame. A naive `count > capacity` rule
would fire dozens of events per minute and make the log useless.

**Decision.** A warning or capacity condition must hold continuously for
`alert_seconds` before firing. Each `(zone, event_type)` pair then has a
`cooldown_seconds` window. Leaving the condition clears the timer; re-entry starts
it again.

**Why.** The operator sees a small number of meaningful events instead of a
per-frame stream. Both values are configuration, so the demo and baseline use
different sensitivities without code changes.

**Cost.** A real but very short spike is deliberately ignored. Tuning
`alert_seconds` too high would miss fast incidents; too low brings the spam back.
The demo uses 1.5 s, the baseline 3 s.

**Rejected.** A moving average of counts (smoother, but harder to explain and to
unit test). Firing every frame (unusable log).

---

## 8. MJPEG for video, one-second polling for JSON

**Context.** The dashboard needs live video and live numbers, locally, with no
extra dependency.

**Decision.** `GET /video` streams `multipart/x-mixed-replace` JPEG frames.
`/api/stats` is polled every second and `/api/events` every two seconds. The
client also detects a stale stream after 4 s and shows an offline state.

**Why.** MJPEG works in a plain `<img>` with no JavaScript, no WebRTC stack and no
signalling server. One-second polling is more than enough for a metrics panel and
is trivial to reason about. A 1 s cadence also keeps CPU for inference rather than
for UI work.

**Cost.** MJPEG has no audio and no compression across frames; polling wastes a
little bandwidth. Both are irrelevant on localhost.

**Rejected.** WebRTC (complex, overkill, needs a signalling path). WebSockets
(low latency, but adds protocol code and reconnect logic for a 1 Hz panel).
Server-Sent Events (good fit, but polling is simpler to explain and debug).

---

## 9. Frame skipping to keep playback near real time

**Context.** The demo clip is 30 fps but the laptop processes about 8 frames per
second. Reading every frame would make a 35 s clip take over two minutes and drift
out of sync with the event timestamps.

**Decision.** `frame_step = round(source_fps / target_fps)`. The worker grabs and
discards the extra frames before reading the one it processes. With
`target_fps = 10` and a 30 fps source, two frames are skipped.

**Why.** Frame skipping keeps playback in the same order of magnitude as wall
clock, so a 35 s pass lasts about a minute on this CPU rather than five minutes,
and dwell timers roughly match what the viewer sees.

**Cost.** Two thirds of the source frames are never detected; a person visible for
only one or two source frames can be missed. FPS is reported honestly as the
measured processed rate, never the target.

**Rejected.** Processing every frame in a separate reader thread with a queue
(faster on paper, more memory and more concurrency to explain). Playing the clip
slowly (dwell timers would no longer match the demo script).

---

## 10. Fixed `pixels_per_meter`

**Context.** Density (people/m²) and speed (m/s) need a pixel-to-metre scale.

**Decision.** One scalar per config: 70 px/m for the night street, 50 px/m for the
corridor. Density is `count / (polygon_area_px / ppm²)`; speed is
`pixel_distance / dt / ppm`.

**Why.** It is one number, visible in the config, easy to explain and easy to
test. With full-height lanes the scale is roughly consistent for the walking area.

**Cost.** Perspective bias: people farther from the camera appear smaller, so the
same physical density reads lower in the background. Speed is likewise biased by
depth. A production system would calibrate a homography per camera. This is
documented as a limitation rather than hidden.

**Rejected.** A full homography (more accurate, but needs calibration points and
more maths than the demo needs). Reporting only counts (loses the density and
speed metrics the brief asks for).

---

## 11. MOT17-04-SDP as the crowd clip

**Context.** The demo needs a dense, visually clear pedestrian scene that YOLO11n
actually detects on CPU.

**Options tested.**

| Candidate | Result |
| --- | --- |
| MOT17-04-SDP, night street | ~14–18 people detected; clear lanes; good visual contrast |
| MOT20-05, dense indoor | YOLO11n found only ~4–5 people; unusable for a density demo |
| Generic stock footage | Licence unclear or too sparse |

**Decision.** Use MOT17-04-SDP, downloaded as `data/clips/demo-crowd.webm`
(960×540, 30 fps, 35 s).

**Why.** It produces realistic occupancy (p50 14, p90 16, max 22) with this
model, and the three-lane geometry maps naturally onto a bottleneck story.

**Cost.** MOT17 is **CC BY-NC-SA 3.0**, non-commercial only. Attribution is in
`data/clips/CREDITS.md` and the README. A commercial deployment would need a
different clip or a camera feed.

**Rejected.** MOT20-05 (too few detections). A synthetic or staged clip (less
convincing, and harder to justify as a real crowd scenario).

---

## 12. A second clip for the false-positive story

**Context.** A demo that only shows alerts proves nothing about specificity. The
brief explicitly asks for normal activity to show false positives.

**Decision.** Ship `configs/normal.json` with the OpenCV `vtest.avi` sample
(renamed `normal-corridor.avi`, 768×576, 10 fps) on port 5001. It runs the same
engine with smaller zones, higher capacities and higher thresholds.

**Why.** The same code path produces **zero events** and occupancy around 4–7 on
quiet corridor footage. That is the control condition: the rules are not simply
always firing.

**Cost.** A second clip adds ~8 MB to the repository and a second server command.
That is a fair price for evidence of specificity.

**Rejected.** Reusing the crowd clip with thresholds raised until nothing fires
(less convincing: the same scene, just desensitised).

---

## 13. Zones and thresholds are configuration

**Context.** The reviewer will ask whether the zones are hard-coded to make the
demo look good.

**Decision.** Polygons, capacities, colours, detector confidence, rule
thresholds, target FPS, scale and storage paths all live in `configs/*.json` and
merge over `app/config.py` defaults.

**Why.** The same engine runs both scenarios. Changing a lane boundary or a
capacity is a JSON edit, not a code change, and the config is visible in the
repository and served by `GET /api/config` for inspection.

**Cost.** No schema validation; a malformed polygon fails at runtime. For a
coursework project with two known configs this is acceptable.

**Rejected.** A database-backed zone editor (out of scope). Hard-coded zones
(impossible to defend as a general analytics tool).

---

## Summary of trade-offs to state honestly

- ~4–7 FPS on the development laptop, not 30 FPS.
- Density and speed are perspective-biased by a single scale factor.
- No MOTA/IDF1 evaluation; detection quality is assessed visually.
- MOT17 clip is non-commercial; YOLO is AGPL-3.0.
- Single camera, single worker, no authentication, Flask development server.
