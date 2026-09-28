# IP Camera Bridge Implementation Plan

> **For agentic workers:** Use test-driven implementation and verification-before-completion.

**Goal:** Build the user's Windows MVP with working simulated sources and an RTSP-ready GUI.
**Architecture:** Single latest RGB frame in shared memory; cancellable source process,
independent fixed-rate webcam process, Qt timer preview and coordinator.
**Tech Stack:** Python 3.13, PySide6, PyAV, NumPy, OpenCV headless, pyvirtualcam, PyInstaller.
**Spec:** `../specs/2026-09-28-ip-camera-bridge-design.md`

## Global Constraints
- Windows 10/11 x64; 720p/1080p, default 720p at 25 fps; one source, video only.
- Credentials never persisted or echoed. Backend installed separately.
- UI never decodes video; all buffers bounded; hung worker can be stopped.
- Real RTSP/Meet/Zalo acceptance remains pending until actually tested.

## Review Focus
- A decoder hung in native code must not hold UI or prevent app close.
- Disconnect during backoff must not spawn another connection.
- Userinfo/query credentials and C-level diagnostics must not leak.
- EOF, missing timestamps and slower/faster files must loop at media speed.
- Repeated Start/Stop, backend absence/busy, output after loss must be predictable.

## Tasks
1. **Pattern and output** — `frames.py`, `output.py`, `config.py`; tests for RGB,
   aspect ratio, sanitization, missing/busy backend and one active publisher.
2. **GUI and file** — `sources.py`, `controller.py`, `ui.py`, `__main__.py`;
   tests for latest frame, file pacing/loop, source lifecycle and GUI responsiveness.
3. **RTSP** — source options, retry state, watchdog; tests for bounded backoff,
   cancel retry and force-stop of hung decoder. Local stream if environment permits.
4. **Delivery** — pinned requirements, run/build scripts, spec, Vietnamese README,
   sample config, manual checklist and evidence report. Run full suite, build and smoke.

For each task: write the relevant failing unittest, run it, implement, then run the
same test to green. Final independent code review, fix material issues, full verification.

## Progress / rulings
- Workspace inspected: empty, no Git repository or project instructions found.
- User already requested execution of the supplied plan; proceed without new approval gates.
- Dedicated `IPCameraBridge` directory provides isolation; no Git/worktree scaffold needed.
- Parallel work: primary agent owns controller/UI/integration; helpers own source/frame
  and output/security modules with explicit interfaces, plus official compatibility research.

- [x] Pattern/RGB/shared slot/output and security: implemented, unit tests verified.
- [x] GUI/file/controller: implemented; offscreen source and executable smoke verified.
- [x] RTSP: TCP/timeouts/reconnect/cancel implemented and mock tested; localhost integration not run: MediaMTX download failed Schannel TLS in sandbox; elevated retry was cancelled while awaiting approval.
- [x] Delivery: pinned dependency set, Vietnamese docs, sample config, scripts/spec, Windows onedir executable.
- [x] Independent review: two material findings reproduced RED then fixed GREEN; no deferred minors.
- Final automated suite: 38/38 passing; pip check clean; final build exit 0; packaged pattern720p/file1080p smoke exit0.
- Ruling: manual source disconnect stops output too so stopped/force-killed producer storage can be replaced safely;
  automatic network loss continues the webcam with a disconnected slate. README/spec synchronized.
- Runtime environment limitation: OBS driver absent, so actual virtual webcam/Meet/camera/Zalo acceptance remains open.
