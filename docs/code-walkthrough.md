# Code walkthrough and defence sheet

Written for the question **"explain the code."** Every claim below points at a
real line in this repository. Numbers were read from the files and the clips, not
remembered.

How to use it:

1. Read Part 0 until you can say it without looking. It is the answer to
   *"walk me through your system"* in about 60 seconds.
2. Read Part 2 once per file, with the file open on the other half of the screen.
3. Do Part 6 (the drill) out loud, no notes. Fix what you stumble on.
4. Keep Part 4 and Part 5 for the questions that come after.

---

## Part 0 — the 60-second answer

> "One background thread owns the video and the model. For each frame it runs
> YOLO11 with ByteTrack, which gives me a box and a stable ID per person. I throw
> away boxes shorter than a threshold, and I hand the rest to a zone engine that
> does pure geometry: is the box centre inside this polygon, how long has this ID
> been here, how fast is it moving. That produces a per-zone snapshot: count,
> density, dwell, speed. A rule engine watches those snapshots and raises an event
> only if the condition held for a set number of seconds and that zone has not
> alerted recently. Events get a JPEG of the annotated frame and a SQLite row.
> Flask never runs inference — it serves the last JPEG as MJPEG and the last stats
> snapshot as JSON, so a slow browser cannot slow the pipeline. The dashboard polls
> once a second and draws DOM, canvas and SVG."

If they only remember one thing: **`app/detector.py` is the only place AI
happens. Everything else is geometry, rules and plumbing — which is why it is
testable.**

---

## Part 1 — the pipeline

```
 video file
     │  camera_worker._run()            (one daemon thread)
     ▼
 cv2.VideoCapture ── skip (frame_step-1) frames ──► read one frame
     │
     ▼
 _resize()  letterbox to 1280×720          ← so overlays always align
     │
     ▼
 detector.detect()  = YOLO11n + ByteTrack  ← THE ONLY MODEL
     │  list[Track]  (id, x1,y1,x2,y2, conf)
     ▼
 zone_engine.update()   point-in-polygon, dwell timers, speed, density
     │  dict[name → ZoneStats]
     ▼
 rule_engine.evaluate() duration gate + cooldown gate
     │  list[Event]
     ▼
 _save_snapshot() + store.add()            ← evidence: JPEG + SQLite row
     │
     ▼
 _update_heatmap() → _build_stats() → _set_state()  (under one lock)
     │  latest jpeg bytes + latest stats dict
     ▼
 Flask:  GET /video  (MJPEG)      GET /api/stats  (JSON)      GET /api/events
     │
     ▼
 app.js: poll 1 Hz → renderStats() → DOM + SVG overlay + canvas heat + chart
```

---

## Part 2 — file by file

### `app/config.py` — 90 lines, no dependencies

**Role:** every tunable number lives here, so nothing is a magic constant buried
in logic.

| Line | What it does | If asked |
| --- | --- | --- |
| 14 | `BASE_DIR = Path(__file__).resolve().parent.parent` | Resolves to the project root from anywhere, so files work regardless of the shell's current directory. |
| 16 | `DEFAULT_CONFIG` | The app can run with no config file at all. |
| 64 | `_deep_merge` | Override only the keys you name; recurse into nested dicts; `deepcopy` so neither input is mutated. |
| 75 | `load_config(path)` | Defaults, then merge the JSON override on top. |
| 87 | `resolve_path` | Absolute path wins, relative path is anchored to `BASE_DIR`. |

**Subtlety worth knowing:** `DEFAULT_CONFIG` uses zones `Gate A / Corridor /
Gate B` with capacity 12/18/12 — but `configs/demo.json` overrides them with
`West lane / Centre lane / East lane` and 8/7/6. The defaults are a fallback,
not the demo.

---

### `app/detector.py` — 61 lines, the only AI

**Role:** wraps Ultralytics so the rest of the app only ever sees `list[Track]`.
Swap the model, nothing downstream changes.

| Line | What it does |
| --- | --- |
| 20 | `from ultralytics import YOLO` **inside** `__init__` — a lazy import, so tests that do not touch the detector never pay the torch import cost. |
| 33 | `self.model.track(...)` — note `track`, not `predict`. This is what gives persistent IDs. |
| 36 | `classes=[0]` — COCO class 0 is person. This is why the model cannot emit "car" or "dog" boxes. |
| 37 | `conf=self.conf` — 0.30 in the demo, 0.40 in the normal baseline. |
| 39 | `tracker="bytetrack.yaml"` — the association algorithm ships with Ultralytics. |
| 54 | `if (y2 - y1) < self.min_box_height: continue` — drops distant/short boxes. 18 px in the demo, 30 px in the baseline. This is the main false-positive control. |
| 57 | `ids[index].item()` — ByteTrack's ID for this box. Falls back to `-1` if `ids` is `None`, and `ZoneEngine` filters `id >= 0` at line 110. |
| 29 | `self.model.predictor = None` — drops tracker state so a looped clip starts with clean IDs. |

**Why ByteTrack specifically?** It associates low-confidence boxes as well as
high ones, so a person who is briefly occluded does not get a new ID — which
matters because dwell time is keyed on ID. The alternative (plain SORT) would
fragment dwell timers on every occlusion.

---

### `app/zones.py` — 172 lines, the core of the project

**Role:** plain geometry over boxes. No ML here at all. This is the file to
show first, because it is the most defensible.

| Line | What it does |
| --- | --- |
| 17 | `@dataclass Track` — `cx` and `cy` are properties (line 29, 33), so the centre is never stale after a box changes. |
| 41 | `@dataclass Zone` — name, normalised polygon, capacity, colour. |
| 49 | `@dataclass ZoneStats` — the per-frame output: count, capacity, density, avg/max dwell, velocity. |
| 61 | `point_in_polygon` — returns `False` if fewer than 3 points; otherwise `cv2.pointPolygonTest(...) >= 0`. |
| 66 | `>= 0` not `> 0` — a point exactly on the boundary counts as inside. |
| 84 | `_entry: (track_id, zone_name) → first-seen time` — the dwell clock. Keyed by **both** ID and zone, so one person can be tracked in two overlapping zones independently. |
| 86 | `_last_pos: track_id → (cx, cy, t)` — the speed reference point. |
| 93 | `_polygons_px` — normalised 0–1 coordinates become pixels: `round(px * frame_w)`. This is why one config works on a 16:9 and a 4:3 clip. |
| 110 | `track.id >= 0` — drops untracked detections. |
| 117–121 | A person is "in" a zone if their **box centre** is inside. Not their feet, not their whole box. |
| 127 | First frame in a zone starts the dwell timer. |
| 129 | `dwell = now - entry` — wall-clock seconds, not frame counts. |
| 137 | `if dt <= 0: continue` — guards a division by zero if two frames share a timestamp. |
| 140 | `math.hypot(dx, dy)` — Euclidean displacement. |
| 141 | `(distance / dt) / pixels_per_meter` — pixels/second converted to m/s using the fixed scale (70 px/m demo, 50 px/m baseline). |
| 143 | `cv2.contourArea` — polygon area in px². |
| 145 | `density = count / area_m2` — people per square metre. |
| 162–164 | **A track that leaves a zone loses its entry timer**, so re-entering restarts dwell. This is tested at `tests/test_zones.py:54`. |
| 167–169 | Drops positions not seen for 3 s, so memory does not grow over a long demo. |

**The two weaknesses to own before they find them:**

- Dwell and speed use **wall-clock** `now`, not video time. If the clip plays at
  1.7× slow motion, "dwell 15 s" means 15 real seconds, which is ~9 s of video.
  Honest answer: *"the metric is real elapsed time, which is what an operator
  cares about; with a live camera the two are identical."*
- Density uses **one** fixed px/m for the whole frame, so the far end of the
  scene is under-scaled. Expected for a single-camera prototype; the fix is a
  homography (Part 5).

---

### `app/rules.py` — 110 lines, the anti-spam layer

**Role:** turns metrics into events, **only** when a condition has been true for
long enough, and **only** if that zone has not alerted recently.

| Line | What it does |
| --- | --- |
| 27–30 | Four knobs from config: `warning_ratio`, `alert_seconds`, `dwell_seconds`, `cooldown_seconds`. |
| 32–34 | Three dicts of state: when over capacity started, when warning started, when each `(zone, type)` last fired. |
| 36 | `_cooldown_ok` — returns `True` and **stamps the time** if the gap is big enough. Note it mutates, so it is only safe to call once a condition is already true (which is how it is used). |
| 51 | `warning_at = warning_ratio * capacity`. Demo ratio is **0.6**, so a capacity-8 lane crosses at 4.8 — and since counts are integers, the first count that warns is **5**. |
| 53 | `count >= capacity` → capacity candidate. |
| 54–55 | First frame at capacity records the start time instead of firing. |
| 57 | Fires only if `now - start >= alert_seconds` (**1.5 s** in the demo). |
| 71 | If the count drops below capacity, the timer is **deleted**, so it must re-accumulate from zero. |
| 73 | Warning band is `warning_at <= count < capacity` — strict upper bound, so warning and capacity never fire together. |
| 93 | Dwell is different: no duration gate, just a threshold on `max_dwell` plus cooldown. |

**Why wall-clock cooldown and not a frame count?** Frame rate varies with CPU
load (5–7 FPS in practice). A cooldown in frames would fire twice as often on a
faster machine. 30 seconds is 30 seconds. This is the answer if they probe it.

---

### `app/camera_worker.py` — 339 lines, the loop

**Role:** one daemon thread owns the source, the model and the state. Flask only
reads a cached JPEG and a cached dict, so requests never block on inference.

| Line | What it does |
| --- | --- |
| 25–26 | Heat grid is 32 × 18 — same 16:9 as the video. |
| 29 | `HEAT_TAU_SECONDS = 4.0` — a cell fades to 1/e in 4 s. |
| 53 | `self._lock` — one mutex protecting the published frame and stats. |
| 57–63 | The published state: JPEG bytes, frame counter, FPS, heat grid, stats dict. |
| 104–107 | `stop()` sets an event and joins with a 3 s timeout. |
| 109–119 | `get_frame()` / `get_stats()` copy under the lock. `deepcopy` at 119 means Flask cannot mutate worker state. |
| 138–154 | `_resize` — letterbox: scale to fit inside 1280×720, then centre on a black canvas. **This is why the SVG overlay always lines up**, including on the 4:3 baseline clip. |
| 187 | `_save_snapshot` — filename `{ms}_{zone}_{type}.jpg`; non-alphanumerics become `_` so names are filesystem-safe. |
| 204–206 | `self._heat *= exp(-elapsed / TAU)` — **time-based** decay. Frame-count decay would fade twice as fast at double the frame rate. |
| 208–210 | `+1.0` at the cell containing the box centre, clamped to the grid. |
| 212–215 | Divides by the peak so values are 0–1 regardless of crowd size. |
| 226–234 | Config ratio decides `ok` / `warning` / `critical` — the same rule as `rules.py`, but for **display**. |
| 266 | `_run` begins; 268 returns early with `offline` if the file will not open. |
| 279 | `source_fps` read from the clip. |
| 280 | `frame_step = round(source_fps / target_fps)`. Crowd clip is **30 fps** → step **3**. Baseline is **10 fps** → step **1**. |
| 286–288 | `capture.grab()` discards `frame_step - 1` frames without decoding them — cheap skipping. |
| 291–301 | End of clip: seek to 0, and **reset the detector and zone engine** so IDs and dwell timers do not leak across a loop. |
| 306–309 | The four-line pipeline: detect → zone update → rule evaluate. |
| 311 | `frame.copy()` for annotation, so the clean frame is available if needed. |
| 312–315 | One snapshot + one DB row per event, then bump the counter. |
| 319 | `cv2.imencode(".jpg", ...)` at quality 82 — encoding happens once per frame, not per viewer. |
| 323–328 | FPS is **time-based and smoothed**: `0.85 * old + 0.15 * instant`. First frame seeds it directly. |
| 336 | `sleep(frame_interval - elapsed)` — targets 10 FPS, sleeps the remainder. If processing already took longer, it sleeps 0 and just runs slower. |

**The single best sentence about this file:** *"The lock is only held while
publishing bytes and stats, never during inference — that is what keeps the web
layer responsive while the model is busy."*

---

### `app/storage.py` — 108 lines, persistence

| Line | What it does |
| --- | --- |
| 24 | `check_same_thread=False` — the DB is written by the worker thread and read by Flask request threads. |
| 20 | `threading.Lock` — because of the above. |
| 47 | Index on `ts DESC` — the dashboard always asks "newest first". |
| 55 | `with self._lock, connection:` — the connection context manager commits/rolls back; the lock serialises threads. |
| 56–70 | Parameterised `INSERT`? placeholders only. No string formatting into SQL anywhere in the repo. |
| 79–87 | `ORDER BY ts DESC, id DESC` — the tie-break on `id` makes ordering deterministic when two events share a timestamp. |
| 101 | `clear()` deletes rows, and `main.py:117` separately purges the JPEGs so no orphans survive. |

---

### `app/main.py` — 177 lines, Flask

| Line | What it does |
| --- | --- |
| 22 | `purge_snapshots` — deletes `*.jpg`, swallowing `OSError` so a locked file cannot crash startup. |
| 41–56 | Wiring: store → detector → zone engine → rule engine → worker. Everything is constructed once at startup. |
| 75–95 | `/video` — a generator that yields a multipart JPEG each time `frame_id` changes. Line 81 is the important one: if the frame has not changed, sleep 20 ms and loop, so a viewer never receives the same JPEG twice. |
| 93 | `multipart/x-mixed-replace; boundary=frame` — the MJPEG convention every browser understands. |
| 97–101 | `/api/stats` returns `worker.get_stats()`, a copy. |
| 109 | `limit` clamped to `[1, 200]` — a client cannot ask for 10 million rows. |
| 114–119 | `/api/events/clear` does three things: delete rows, purge JPEGs, reset the session counter. All three, or the demo shows stale evidence. |
| 143–147 | `/snapshots/<path:filename>` uses `send_from_directory`, which refuses paths that escape the directory — no `../` traversal. |
| 173 | `threaded=True, use_reloader=False` — reloader would spawn a second worker and double the model. |

---

### `static/js/app.js` — the dashboard

| Line | What it does |
| --- | --- |
| 10 | `STALE_MS = 4000` — four missed polls before the UI admits the server is gone. |
| 69–71 | Three timers: clock 1 s, stats 1 s, events 2 s. No websockets, no framework. |
| 116 | `fetchStats` — `cache: "no-store"` so the browser cannot serve a stale JSON body. |
| 153 | `live = status === "live" && frame.w > 0` — a responding server with a dead video is still OFFLINE. |
| 203–210 | History keeps **60** points and `shift()`s the oldest — a rolling 60 s window matching the 1 Hz poll. |
| 265–337 | `renderZoneOverlay` — rebuilds SVG polygons from normalised coords into a `0 0 160 90` viewBox (multiply by 160 and 90). Line 307 computes the centroid for the label. |
| 368–402 | `renderHeatmap` — sizes the canvas by `devicePixelRatio` for sharpness, then one `fillRect` per cell, skipping values below 0.06 to avoid a grey wash. |
| 391 | `values[row * cols + col]` — row-major, matching numpy's `flatten()` on the Python side. |
| 589–626 | `heatColor` — gamma 0.6 lifts the long tail, three-stop gradient cyan → amber → rose, alpha grows with intensity. |
| 573–587 | `checkStale` — the badge flips to OFFLINE on its own, using page-load time as the baseline if no stats ever arrived. |

---

## Part 3 — the numbers that prove the story

These are the arithmetic checks you can do live if someone doubts a claim.

| Claim | Arithmetic | Result |
| --- | --- | --- |
| Crowd clip is 35 s | `1050 frames / 30 fps` | 35.0 s ✓ (measured from the file) |
| It skips two of three frames | `round(30 / 10)` | `frame_step = 3` ✓ |
| Analysed frames per pass | `1050 / 3` | ~350 |
| One pass takes ~57 s | `350 / 6.2 FPS` | ~56 s ✓ (matches measured 57 s) |
| Baseline does **not** skip | `round(10 / 10)` | `frame_step = 1` ✓ |
| Baseline pass, shared CPU | `795 / 4.1 FPS` | ~194 s = 3.2 min ✓ |
| Baseline pass, alone | `795 / ~5.3 FPS` | ~150 s = 2.5 min ✓ |
| Warning at 4.8 people, West lane | `0.6 × 8` | 4.8 — but counts are integers, so it warns at **5** |
| Heat fades in 4 s | `TAU = 4.0` | 1/e at 4 s ✓ |

**If caught claiming a fixed event count:** event counts are load-dependent
(wall-clock 30 s cooldown on a variable frame rate). Always quote a range —
1–7 per 60 s, 7–16 per pass.

---

## Part 4 — likely questions, with answers

**"Where is the AI?"**
`app/detector.py`, one `model.track()` call at line 33. Everything else is
geometry and rules, which is why 19 tests run in 0.37 s without a model.

**"Why ByteTrack and not SORT or DeepSORT?"**
ByteTrack associates low-confidence detections too, so brief occlusion does not
create a new ID. Dwell is keyed on ID, so ID churn would break dwell. DeepSORT
adds a ReID network — more CPU for no benefit at this crowd density.

**"Why 0.6 warning ratio?"**
`rules.py:51`. 0.6 means an operator sees a lane filling *before* it jams. At 0.8
you get less warning; at 0.5 every lane is permanently amber and the signal is
useless. It is a config value, not a constant — `configs/normal.json` uses 0.8
because that scene should stay quiet.

**"Why is the cooldown wall-clock and not frames?"**
Measured FPS swings 4–7 with CPU load. A frame-count cooldown would fire twice as
often on a fast machine. `rules.py:39` compares seconds.

**"Why annotate boxes on the server instead of drawing them in the browser?"**
One encoder, one image. The browser receives a single JPEG and does not need the
box list, so `/api/stats` can stay small and the MJPEG stays cheap. Client-side
boxes would need per-frame geometry over the wire at 10 Hz.

**"Why normalised zone coordinates?"**
`zones.py:93` multiplies by the frame size at runtime, so the same `demo.json`
works on the 16:9 letterboxed output and a 4:3 source. Hard-coded pixels break the
moment the resolution changes.

**"Why letterbox to 16:9?"**
`camera_worker.py:138`. The SVG overlay is a fixed `0 0 160 90` viewBox; forcing
every frame to 16:9 means overlay and video share one coordinate system with no
per-clip maths.

**"What happens if two people cross?"**
ByteTrack may swap IDs; dwell would then split. Accepted limitation at this
scale — the alternative is a ReID model and a much slower loop.

**"What if someone stands still?"**
`zones.py:129` keeps accumulating while the centre is inside, so a dwell event
fires at `dwell_seconds` (30 s demo, 120 s baseline).

**"Why SQLite and not Postgres?"**
One table, one writer, local demo. SQLite is a file, needs no service, and
`storage.py` is 108 lines. Postgres would add an install step and nothing the
rubric rewards.

**"How do you know it is correct?"**
Two ways. Unit level: 19 tests cover polygon membership, density arithmetic,
dwell reset on exit, the duration gate, the cooldown, and config validity. System
level: the `normal-corridor` baseline is a clip with no congestion and it
produces **zero events** — that is a specificity test, not an accuracy score.

**"Do you have MOTA / IDF1?"**
No, and I will not claim one. The clips have no labelled evaluation split. Visual
inspection plus the zero-event baseline is the evidence. An honest gap, listed in
`README.md` limitations.

**"Why is FPS only 5–7 when the target is 10?"**
The target is a *cap*, not a promise. `camera_worker.py:336` sleeps the
remainder; if inference already took longer it sleeps 0 and simply runs slower.
This is a CPU-only laptop with the nano model, and one worker is one core.

**"What is the heat map actually made of?"**
A 32×18 float grid. Each frame adds 1.0 at the cell under each person's centre,
and everything decays by `exp(-dt/4.0)`. So it shows where people *have been*
over roughly the last ten seconds, not just this frame.

**"Why not use a webcam?"**
Reproducibility. A file plays the same way every time and loops; a webcam makes
timing and event counts unrepeatable in front of an examiner. `--source 0` works
if needed — documented, not the demo path.

**"What would you change with another week?"**
Homography for perspective-correct density, a labelled split with MOTA/IDF1, a
zone editor instead of JSON, and a production WSGI server with auth.

---

## Part 5 — honest weak spots

Say these before you are asked. It reads as judgement, not as a gap.

1. **`Track.height` and `Track.conf` are stored but never read.** `detector.py:54`
   measures `y2 - y1` inline instead of using the property. Answer: *"carried for
   completeness; the filter does the check inline. I would delete both or use
   them."*
2. **`ZoneStats.polygon_px` is populated but not consumed** — `_build_stats`
   reads the polygon from config instead. Same answer: redundant field.
3. **Dwell and speed are wall-clock**, so at 1.7× slow motion they overstate
   video-time dwell. Only true of a file source.
4. **Single px/m scale** — perspective bias, no homography.
5. **No accuracy metric.** Specificity only, via the baseline.
6. **`fetchStats`'s catch block duplicates `checkStale`** — two paths that can
   set `stale`. Harmless, mildly redundant.
7. **Peak occupancy resets on refresh** — it is client-side `Math.max`, never
   persisted server-side.
8. **One worker, one core.** Two servers on one laptop halve each other to
   ~4.3 FPS; that is measured, not theoretical.

---

## Part 6 — the drill

Do this out loud, screen off, in this order. If you stall, note the file name
only, then check Part 2.

**Round 1 — no code on screen (5 min)**
1. The 60-second answer, once, clean.
2. Name the seven modules and one sentence each.
3. Which single file contains the model? Which four lines form the per-frame
   pipeline?

**Round 2 — `zones.py` open (5 min)**
4. Walk `update()` top to bottom without skipping.
5. Why key `_entry` by `(id, zone)` and not just `id`?
6. Where does density get its unit, and what breaks it?
7. Point at the line that makes re-entry reset dwell.

**Round 3 — `rules.py` open (4 min)**
8. Why does the first frame at capacity not fire?
9. What exactly deletes a timer, and why does that matter?
10. Why can warning and capacity never fire in the same frame?

**Round 4 — `camera_worker.py` open (6 min)**
11. Where is the lock held, and where is it deliberately not held?
12. Why `grab()` before `read()`?
13. What resets when the clip loops, and what happens if it does not?
14. Which line guarantees the overlay lines up on a 4:3 clip?

**Round 5 — hostile (5 min)**
15. *"Your dwell number is wrong."* — concede the wall-clock point, explain why
    it is correct for a live camera.
16. *"Show me a line that exists for no reason."* — `Track.height`, and own it.
17. *"Prove the FPS."* — do the 1050 / 3 / 6.2 arithmetic out loud.

---

## Part 7 — if they point at a line you do not recognise

Don't guess. Use this, it takes ten seconds and sounds competent:

1. Read the **function name** above the line.
2. Read the **variables** it touches — they usually name the intent.
3. Say what it does *mechanically* before what it is *for*.
4. Finish with the purpose in one clause: *"...and that is so the overlay cannot
   drift from the video."*

If it is genuinely unclear: **"That is defensive rather than load-bearing — it
guards against X. I can pull it up in the repo if you want to see it in
context."** Never invent a reason for a line you cannot read.

---

## Related documents

- `docs/architecture.md` — component diagram and cost table.
- `docs/decisions.md` — every rejected alternative, with the reason.
- `docs/demo.md` — the 12-minute run of show and the Q&A for concepts.
