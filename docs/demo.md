# Demo script

A 12-minute walkthrough for **Wednesday 11 November 2026**. The plan assumes one
laptop, two terminal windows and two browser tabs. Have the architecture diagram
open as a PDF or image in a third tab, and the README open in an editor.

---

## Pre-flight (15 minutes before)

1. Plug in the laptop. Set power mode to **Best performance** and disable sleep.
2. Close unused Chrome tabs and any other CPU-heavy app. Inference is CPU-bound.
3. Open PowerShell in the project folder and activate the environment:

   ```powershell
   cd "C:\Users\user\Documents\Default Project\cognovalley-crowd-ops"
   .venv\Scripts\Activate.ps1
   ```

4. Confirm the model weights exist locally so nothing downloads during the demo:

   ```powershell
   Test-Path .\yolo11n.pt
   ```

5. Free the ports if an old server is still running:

   ```powershell
   Get-NetTCPConnection -LocalPort 5000,5001 -State Listen -ErrorAction SilentlyContinue |
     Select-Object LocalPort,OwningProcess
   ```

   If something is listening, stop it by PID:

   ```powershell
   Stop-Process -Id <PID> -Force
   ```

6. Start the crowd demo **with `--reset`** (this deletes old events and evidence
   images):

   ```powershell
   python -m app.main --config configs\demo.json --port 5000 --reset
   ```

   Measured cold start: Flask answers after **~8 s** and the first annotated
   frame is ready at **~15 s**. Start it before you need it on screen.

7. Open <http://127.0.0.1:5000> and let it run for at least 60 seconds. Check:

   - status badge says **LIVE**, not OFFLINE;
   - FPS settles around **5–7**;
   - occupancy is roughly **10–18**;
   - boxes follow people and the three lanes are visible.

8. Start the baseline on a second port (second terminal, same environment):

   ```powershell
   python -m app.main --config configs\normal.json --port 5001 --reset
   ```

   Open <http://127.0.0.1:5001> after **15–20 s** (cold start is slower with the
   demo worker already running) and confirm **0 events**, occupancy around
   **4–8**.

   **Two inference workers share this CPU.** Measured with both running:
   **4.0–4.5 FPS each**, down from 5–7 for the demo alone, and a baseline pass
   stretches to **~3.2 minutes**. So do not cold-start it at 8:00 and wait for a
   full pass — it will not finish inside a 2-minute section. Start it at **7:00**
   instead, once the crowd pass has ended, and it will have run about two and a
   half clean minutes by the time you switch to it.

9. Leave both tabs open. Do not leave the crowd tab running for many minutes
   before you start: the event log will fill and the clip will loop. If it does,
   press **Clear** in the event log and let one fresh pass run (~60 s).

---

## Run of show

### 0:00–1:00 — Opening

Say:

> "This is Track A: crowd density and bottleneck analytics for a fixed camera.
> It detects and tracks people, measures how crowded configurable zones are, and
> raises debounced operator alerts with visual evidence. It runs entirely on
> this laptop, on CPU, with no cloud service."

Point at the dashboard once, then move on. Do not read the feature list yet.

### 1:00–2:00 — Problem and scope

Say:

> "A venue with fixed cameras already has video, but nobody watches it all. The
> useful questions are: how many people are in this area, is it filling up, is
> anyone lingering, and where is the crowd concentrating? This system answers
> those four questions and turns them into an event log an operator can act on."

State the scope honestly:

- one camera, one process;
- zones are configured per camera;
- this is a demonstration on recorded clips, not a certified safety system.

### 2:00–4:00 — Architecture (use the diagram)

Walk the arrows left to right:

1. A background Python worker decodes the clip, letterboxes it to 16:9 and skips
   frames to about 10 processed frames per second.
2. YOLO11n detects people; ByteTrack keeps a stable ID per person.
3. The zone engine tests each box centre against the configured polygons and
   computes count, density, dwell time and speed.
4. The rule engine applies duration debounce and a cooldown, then writes an event
   row to SQLite and an annotated JPEG as evidence.
5. Flask only reads the newest published frame and stats. It never runs
   inference, so polling cannot block the video.
6. The browser is plain HTML/CSS/JS: MJPEG for video, JSON polling for numbers,
   SVG for zones, canvas for the heat map.

One sentence to land: **"The request thread and the inference thread are
separate, which is why the UI stays responsive when YOLO is slow."**

### 4:00–8:00 — Live crowd demo (tab 5000)

Do this in order; each item is visible on screen.

1. **Video panel.** "These boxes are drawn server-side. The ID above each box
   comes from ByteTrack. The yellow tint marks the zones."
2. **KPI row.** Point out current occupancy, peak, alert count and measured FPS.
   Say the measured FPS out loud, e.g. "about 7 frames per second on this CPU".
3. **Zone panel.** "West lane, Centre lane, East lane. Each has a capacity. The
   bar is count over capacity; the status changes from ok to warning to
   capacity."
4. **Heat map.** Toggle it with the **Heatmap** control (or open `/#heat`). "This is
   a 32 by 18 accumulator that fades over about four seconds and adds one at each
   person's centre. It shows where the crowd concentrates over time, not just
   this frame."
5. **Trend chart.** "Sixty seconds of total occupancy. Spikes are visible here
   before the event log fills."
6. **Event log.** Wait for the first event, then click one. "Every row has zone,
   type, severity, count, timestamp and a snapshot. This is the evidence the
   operator sees." Open the snapshot.
7. **Clear and fresh pass.** Press **Clear**, confirm. "The log, the evidence
   images and the session counter reset, so we can show a clean run." Let one
   pass play — about **60 s** at the measured frame rate, not 35 s.

Expected within one fresh pass of the 35 s clip:

- occupancy p50 around **14**, p90 around **16–17**, peak in the low 20s;
- **warning and capacity events in all three lanes**, plus a **dwell** event after
  roughly 30 s of continuous presence. Order depends on where the loop starts;
- about **6 events** in the first minute is the *typical* figure, not a promise:
  a 60 s window ranged from **1 to 7** live, and a full pass produced **7** on a
  quiet machine and **16** when loaded. The first event after a Clear arrived in
  **7.9 s**. State a range, not an exact number.
- FPS **5–7** with this tab open;
- one pass takes about **60 s** at 6 FPS, because the worker runs below its
  10 FPS target and skips two of every three source frames. Wait for the pass to
  end before the next section, or the Clear demo will not have shown a full loop.

Say: **"The thresholds are in `configs/demo.json`, not in the code. The same
engine runs the next clip with different numbers."**

**Cue at 7:00 — after the fresh pass finishes, start the baseline** (second
terminal):

```powershell
python -m app.main --config configs\normal.json --port 5001 --reset
```

It needs 15–20 s to warm up and about three minutes for a full pass, so starting
it once the crowd pass has ended is what makes the 8:00 section honest without
stretching the pass to ~81 s. The demo tab drops to ~4.3 FPS from here; you have
already quoted the FPS number.

### 8:00–10:00 — Baseline / false-positive check (tab 5001)

Switch to <http://127.0.0.1:5001>.

Say:

> "A detector that alerts on everything is useless. This is the control run: the
> same code, a quieter corridor clip, smaller zones with higher capacities and
> higher thresholds. The dashboard is live and counting people, but the event log
> is empty."

Point at:

- status **LIVE**, occupancy around **4–8** (p50 5, max 8 in testing);
- every zone status **ok**;
- **0 events** in the ~2.5 minutes it has been running since 7:00. A complete
  795-frame pass takes **~3.2 minutes** at this shared frame rate and produced
  **0 events** in every full-pass test, live and offline. Say "no events in two
  and a half minutes of running" — do not claim a finished pass you did not watch
  finish.

Then state the honest limit: **"This shows the rules do not fire on normal
activity. It does not prove zero false positives in every scene; boundary
flicker and occlusion can still produce a count change."**

### 10:00–11:30 — Code walkthrough (three files, ~30 s each)

90 seconds does not fit four files at 30–60 s. Do these three; mention the fourth
in one sentence if time is left.

1. `app/zones.py` — `ZoneEngine.update()`: point-in-polygon on box centres.
2. `app/rules.py` — `RuleEngine.evaluate()`: the duration timer and the cooldown
   check.
3. `app/camera_worker.py` — the loop: read, skip, detect, update, evaluate,
   snapshot, publish under the lock.
4. *(only if ahead)* `static/js/app.js` — `renderStats()`: plain DOM writes, no
   framework.

If asked "where is the AI?", answer: **"`app/detector.py`, one `model.track()`
call. Everything else is geometry and rules, which is why it is testable."**

### 11:30–12:00 — Limitations and close

Say:

> "Known limits: about 5 to 7 FPS on this CPU, so it is near real time but not
> smooth. Density and speed use one fixed pixel-to-metre scale, so they are
> biased by perspective. There is no MOTA or IDF1 evaluation because the clips
> have no hand-labelled split. The MOT17 clip is non-commercial and YOLO is
> AGPL-3.0; both are documented. Next steps would be per-camera homography, a
> multi-camera aggregator and a production WSGI server."

Close with the value sentence: **"It turns passive camera footage into a small,
evidence-backed list of things an operator should look at."**

---

## Exact commands (copy-paste)

Crowd demo:

```powershell
python -m app.main --config configs\demo.json --port 5000 --reset
```

Baseline:

```powershell
python -m app.main --config configs\normal.json --port 5001 --reset
```

Tests:

```powershell
pytest -q
```

Offline replay if the browser or Flask misbehaves:

```powershell
python scripts\tune_zones.py configs\demo.json
python scripts\tune_zones.py configs\normal.json
```

---

## Fallback plan

| Failure | What to do |
| --- | --- |
| Port already in use | Find the PID with `Get-NetTCPConnection -LocalPort 5000 -State Listen`, then `Stop-Process -Id <PID> -Force`. Restart with `--reset`. |
| Model weights missing / no network | `yolo11n.pt` is already in the project root on the demo laptop. If it is missing, copy it in before the demo; do not rely on a download. |
| Server starts but video is black | Check the terminal for `cannot open`; confirm `data/clips/demo-crowd.webm` exists. Restart. |
| FPS collapses below 4 | Close other Chrome tabs and apps; the worker is CPU-bound. Do not claim a higher number. |
| Dashboard shows OFFLINE | The stats poll has missed 4 s. Check the terminal; restart with `--reset`. |
| Everything fails live | Run the offline replay commands, then show `docs/assets/dashboard-desktop.png`, `docs/assets/normal-baseline.png` and the architecture diagram. Explain the results from the replay output. |
| Asked for a webcam | Explain the clip is used for reproducibility and fair timing; a webcam index can be set as `source` in the config or passed as `--source 0`, but it is not the demo path. |
| Asked to analyse their own clip | `python -m app.main --config configs\any-clip.json --source "<absolute path>"`. Explain that the single full-frame zone is used because the demo lanes are camera-specific, and that it writes to a separate database. |

---

## Q&A preparation

**"Is this real time?"**
Not quite. It processes about 5–7 frames per second on this CPU against a target
of 10, and skips two of every three source frames, so the 35 s crowd clip takes
roughly a minute to play through — about 1.7× slower than real time. It is not a
30 FPS system, and it would not keep up with a live camera at full frame rate.
Asked bluntly: near real time, but noticeably slow motion.

**"How accurate is it?"**
Detection and tracking quality are assessed visually; there is no MOTA/IDF1
number because the clips have no small labelled evaluation split. Do not claim
benchmark accuracy. The baseline run is evidence of specificity, not accuracy.

**"Why not use a line-crossing counter?"**
Track A is about density and bottlenecks, not footfall. The zone engine could be
extended with line events, but the current metrics answer the brief directly.

**"What happens if someone stands still?"**
Their box centre stays in the zone, so the dwell timer keeps running and a dwell
event fires at `dwell_seconds` (30 s in the demo).

**"Why three vertical lanes?"**
They are a simple, explainable bottleneck model for this camera: three walking
corridors with different capacities. The polygons are configuration, so a real
site would draw its own.

**"Where are the zones defined?"**
`configs/demo.json` and `configs/normal.json`, as normalized 0–1 polygons. The
engine converts them to pixels for the letterboxed frame.

**"Why is the video letterboxed?"**
So the output is always 16:9. The browser can then place the SVG zone overlay and
the heat canvas on the same geometry for both the 16:9 and the 4:3 clip.

**"How do you stop alert spam?"**
A condition must hold for `alert_seconds`, and the same `(zone, type)` pair has a
`cooldown_seconds` window. Both are in the config.

**"Is this private?"**
Processing is local; no frame leaves the machine. Evidence JPEGs are stored on
disk until **Clear** or `--reset`; a production deployment would need a retention
policy and access control.

**"Why YOLO and what about the licence?"**
YOLO11n is small and fast on CPU. Ultralytics is AGPL-3.0; this repository is
public and ships the source, which fits coursework. For a closed product, swap
`PersonDetector` to YOLOX (Apache-2.0); the rest of the pipeline is unchanged.

**"What would you do with more time?"**
Per-camera homography for perspective-correct density and speed, multi-camera
aggregation, a proper evaluation split with MOTA/IDF1, a zone editor, and a
production WSGI server with authentication.

---

## One-page cheat sheet

| Thing | Value |
| --- | --- |
| Crowd dashboard | <http://127.0.0.1:5000> |
| Baseline dashboard | <http://127.0.0.1:5001> |
| Crowd config | `configs/demo.json` |
| Baseline config | `configs/normal.json` |
| Any local clip | `configs\any-clip.json` + `--source "<absolute path>"` |
| Crowd clip | `data/clips/demo-crowd.webm` (960×540, 30 fps, 35 s) |
| Baseline clip | `data/clips/normal-corridor.avi` (768×576, 10 fps, 79.5 s) |
| Demo capacities | West 8, Centre 7, East 6 |
| Demo rules | warning 0.6, alert 1.5 s, dwell 30 s, cooldown 30 s |
| Measured FPS | 5–7 live, ~9.5 worker-only, 4–4.5 each with two servers, 5–9 offline |
| One pass | ~60 s crowd clip, ~3.2 min baseline while both run |
| Tests | 19 passing (`pytest -q`) |
| Clear log | Event log → **Clear** → confirm |
| Reset everything | restart with `--reset` |
