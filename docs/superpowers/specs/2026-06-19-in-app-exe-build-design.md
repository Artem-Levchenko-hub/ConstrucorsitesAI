# In-app Windows installer build — design

**Date:** 2026-06-19
**Owner:** Артём Левченко
**Status:** Approved design → implementation plan next
**NORTH STAR fit:** «без потолка» (Omnia сама расширяет, что может отдать пользователю — от zip-исходников до готового установщика) + высокая геймификация (живая сборка как награда).

## Problem

Today a downloadable Python project ships a zip with source + a `build_installer.py`
the user must run locally (needs Python + PyInstaller installed). The owner wants the
**platform** to build the artifact server-side and hand back a **ready Windows
installer** — "собрать установщик прямо в приложении и скачать готовый exe" — with the
model making the build decisions itself and self-healing when a build fails.

This is the first concrete slice of the broader "self-extending model" direction: the
system derives the build configuration and repairs build failures without the user
touching a toolchain.

## Goals

- One button in the workspace → server builds a **Windows installer (`Setup.exe`)** for
  a Python project → user downloads the ready file. No local Python/PyInstaller needed.
- The build **configuration is auto-derived** by the system (entry point, GUI-vs-console,
  app name, data files, installer metadata) — not hand-authored.
- On build failure, a **self-heal loop**: the model reads the error, patches the build
  spec / code, retries (≤3), then fails honestly with the log + a zip fallback.
- Build UX is **gamified**: live staged progress + log stream + a reward on success.

## Non-goals (YAGNI)

- ❌ macOS / Linux targets — Windows `.exe`/`Setup.exe` only.
- ❌ Node / web (Next.js entity) projects — Python only. (Web→desktop via Electron/Tauri
  is a separate, later pipeline.)
- ❌ Code signing / notarization — the installer is unsigned; the UX warns about
  Windows SmartScreen ("неизвестный издатель → Подробнее → Выполнить в любом случае").
- ❌ Custom icon authoring beyond auto-detection of an icon asset already in the project.
- ❌ Multi-file / directory PyInstaller mode — `--onefile` payload only.

## Target platform & toolchain decision

| Concern | Decision | Why |
|---|---|---|
| Binary target | Windows `.exe` only | ~90% of RU desktop users; tightest scope. |
| App binary | **PyInstaller under Wine** | PyInstaller is NOT a cross-compiler — a Windows `.exe` must be built on Windows or under Wine. Prod is a Linux VPS → Wine. |
| Installer | **NSIS (`makensis`)** | `makensis` runs **natively on Linux** — no extra Wine hop to wrap the payload. (Inno Setup would need Wine; rejected to keep the build container simpler.) |
| Where it runs | **Ephemeral Wine container via the orchestrator** | Self-hosted, no external dependency, reuses the orchestrator's per-project container machinery + MinIO + RQ. Matches NORTH STAR "open, self-sufficient, no lock-in". |

Rejected alternatives: GitHub Actions Windows runner (external dependency, latency,
leaks user code, ties prod to GitHub availability); a dedicated always-on Windows VM
(extra infra + ops + cost, overkill for MVP).

## Architecture

Pipeline (happy path):

```
Web «Собрать .exe»  →  API build_exe_job (RQ)  →  Wine container
   (PyInstaller --onefile → app.exe) → (makensis → Setup.exe)
        →  MinIO (artifacts + presigned URLs)  →  Web: live progress + Download
```

On a PyInstaller/NSIS failure the worker enters the **self-heal loop** (model patches
the spec/code → retry, ≤3) before falling back to the honest-failure card.

### Components

Each unit has one clear purpose, a narrow interface, and is independently testable.

#### 1. `omnia-exe-builder` image — *(zone D / infra)*
- **What:** Docker image = `tobix/pywine` base (Wine + Windows Python) **+ PyInstaller +
  NSIS (`nsis` apt pkg)** in one layer. Contains a small `entrypoint` that, given a
  mounted source dir + a build-spec JSON, runs PyInstaller then makensis and writes the
  artifacts to a mounted output dir.
- **Interface:** invoked by the orchestrator with: input dir (source), spec file
  (`build_spec.json` + generated `app.spec` / `installer.nsi`), output dir. Exit code +
  combined stdout/stderr log; artifacts written to output dir.
- **Depends on:** the Docker image only. Network is **two-phase** (see Security): a short
  dep-install phase reaches the package index, then egress is dropped for the
  PyInstaller/NSIS phase that executes user code.

#### 2. Orchestrator `POST /build-exe` — *(zone D)*
- **What:** spins one ephemeral `omnia-exe-builder` container, mounts source + spec in /
  output out, runs it with a hard timeout (300s) + CPU/mem caps + non-root + `--rm`,
  collects `{exit_code, log, artifacts: {app_exe, setup_exe}}`.
- **Interface:** `POST /build-exe { project_id, commit_sha, spec }` → `{ status, log,
  artifact_paths }`. Token/owner check is done upstream in the API; the orchestrator
  endpoint stays internal (same trust boundary as existing orchestrator endpoints).
- **Depends on:** Docker engine + the `omnia-exe-builder` image; the existing
  container-lifecycle helpers.

#### 3. `services/exe_build.py` — build-spec generator *(zone B / api)*
- **What (the "model decides" core):** pure, deterministic function
  `build_spec(files) -> BuildSpec` deriving:
  - `entry` — **reuse `run_bundle._pick_python_entry`** (already correct: excludes
    build/installer/test tooling, real `__main__` guard).
  - `windowed` — `True` if the entry/deps import a GUI lib (pygame, tkinter, PyQt, …),
    else console.
  - `name` — from project slug / a `__main__` title; `version` default `1.0.0`.
  - `datas` — bundle obvious asset dirs (`assets/`, `images/`, `sounds/`, `data/`,
    `fonts/`) as `--add-data`.
  - `icon` — an `*.ico` already in the project, else none.
  - `requirements` — the project's `requirements.txt` (installed inside the container).
  - NSIS metadata — install dir (`$PROGRAMFILES\<name>`), Start-Menu + Desktop shortcut,
    uninstaller.
- **Interface:** `build_spec(files: dict[str,str]) -> BuildSpec` (a dataclass) and
  `render(spec) -> {"app.spec": str, "installer.nsi": str, "build_spec.json": str}`.
- **Depends on:** `run_bundle` (entry pick). No I/O, no model — fully testable.

#### 4. `build_exe_job` — RQ job *(zone B / api worker)*
- **What:** orchestrates the async flow: read committed files from git → `build_spec` →
  call orchestrator `/build-exe` → on failure run the self-heal loop → on success upload
  artifacts to MinIO → emit SSE events at each stage. Mirrors existing build/preview jobs.
- **Interface:** enqueued by `POST /api/projects/{id}/build-exe`; publishes SSE events
  `exe.stage`, `exe.heal`, `exe.ready{setup_url, exe_url, size, name}`, `exe.failed{log}`.
- **Depends on:** orchestrator client, MinIO client, the self-heal service, SSE bus.

#### 5. Self-heal loop — `services/exe_doctor.py` *(zone B + C)*
- **What:** on a non-zero build, parse the tail of the log, send
  `{error, current_spec, entry_source, requirements}` to a model role `exe_doctor`
  (cheap model first, escalate on repeat) → model returns a **structured patch**:
  extra `hidden_imports`, `collect_data`/`collect_all` packages, a `requirements` pin,
  or a small `build_installer.py`/code fix. Apply → re-render spec → retry. **Cap 3.**
  Each attempt is streamed to chat as a heal stage ("Чиню: добавляю pygame в сборку…").
- **Interface:** `heal(error_log, spec, sources) -> BuildSpec | None` (None = give up).
- **Depends on:** the LLM gateway role config (`exe_doctor`), a strict output schema.

#### 6. MinIO storage + delivery
- **What:** artifacts under `exe/<project_id>/<build_id>/{<name>.exe, <name>-Setup.exe}`.
  Delivery via owner-scoped API proxy (or short-lived presigned URL). Size cap ~150 MB.
  Retention: keep only the latest build per project (+7-day TTL sweep).
- **Interface:** `GET /api/projects/{id}/exe/{build_id}/{artifact}` (owner-scoped, 404
  foreign) → streams the file with `Content-Disposition: attachment`.

#### 7. Web UX — *(zone A)*
- **What:** a **«Собрать .exe»** button next to the existing «Скачать» in the workspace.
  Click → enqueue → **gamified live progress**: an ordered stage checklist
  (Готовлю окружение → Ставлю зависимости → Собираю приложение → [Чиню: …] →
  Упаковываю установщик → 🎉 Готово), a live build-log stream (reuse existing SSE), and a
  reward state on success: "🎉 Готов RetroSnake-Setup.exe — 24 МБ" with a big
  **Скачать установщик** button + a secondary «или портативный .exe» link. A SmartScreen
  note is shown under the download.
- **Interface:** subscribes to the `exe.*` SSE events for the project; renders stages
  from `exe.stage`/`exe.heal`, the download from `exe.ready`, the failure card from
  `exe.failed` (log + «Скачать исходники (zip)» fallback = current bundle).

## Data flow

1. User clicks «Собрать .exe» → `POST /api/projects/{id}/build-exe` (owner-scoped,
   rate-limited) → enqueues `build_exe_job`, returns `{build_id}`.
2. Web subscribes to SSE for the project.
3. Worker: read files at `current_snapshot` commit → `build_spec(files)` → render
   `app.spec` + `installer.nsi`.
4. Worker → orchestrator `/build-exe` → ephemeral Wine container: `pip install` (gated)
   → PyInstaller `--onefile` → `app.exe` → `makensis installer.nsi` → `Setup.exe`.
5. **If non-zero:** `exe_doctor.heal(...)` → patched spec → back to step 4 (≤3). After the
   cap → `exe.failed` with the log.
6. **On success:** upload both artifacts to MinIO → `exe.ready{setup_url, exe_url, size,
   name}` → Web shows the download + reward.

## Security / limits

- **Arbitrary user code executes during the build** (PyInstaller's analysis imports
  modules, running top-level code). Network is therefore **two-phase**: phase 1 installs
  `requirements.txt` with egress restricted to the package index (PyPI / a mirror); phase
  2 — PyInstaller analysis + makensis, the part that runs user code — runs with **egress
  dropped**. The container is also **non-root**, CPU/mem-capped, **300 s timeout**, and
  **ephemeral** (`--rm`). Same posture as the existing user dev-containers.
- **Per-user rate limit** on `POST /build-exe` (reuse slowapi).
- **Owner-scoped** request + artifact endpoints (404 for a foreign/unknown project).
- **Unsigned installer** → the UX explicitly explains the SmartScreen prompt.
- **Feature flag `USE_EXE_BUILD`** (instant rollback), default OFF → canary to the owner
  account first.

## Testing

- **Unit (`services/exe_build.py`):** entry pick, windowed-vs-console detection, asset-dir
  collection, name/version, NSIS-metadata rendering — pure + deterministic.
- **Unit (`services/exe_doctor.py`):** patch-parser (model output → spec delta), capped
  loop logic with a mock model (give-up after 3, applies hidden-imports, etc.).
- **Integration:** build the real snake game in the `omnia-exe-builder` container → assert
  both artifacts are PE32 (`MZ` magic; `file` → "PE32 executable … MS Windows"), and that
  `Setup.exe /S` (silent) under Wine installs the app to the target dir.
- **E2E (prod canary):** build snake under the owner account → download `Setup.exe` →
  verify the PE header + size > 0.

## Rollout

1. Land the pure pieces first (`exe_build.py` + tests) — no infra needed.
2. Build + publish the `omnia-exe-builder` image; add orchestrator `/build-exe`.
3. Wire `build_exe_job` + MinIO delivery + API endpoint behind `USE_EXE_BUILD=off`.
4. Add `exe_doctor` self-heal.
5. Web UX (button, SSE stages, reward, fallback).
6. Canary `USE_EXE_BUILD=on` for the owner; E2E the snake build end-to-end; then widen.

## Future (explicitly later, not v1)

- Real code signing / a purchased cert to kill the SmartScreen warning.
- Node→exe (pkg/nexe) and web→desktop (Electron/Tauri) pipelines.
- macOS `.app` / `.dmg` via a macOS CI runner.
- Richer installer (license page, custom branding, auto-update).
