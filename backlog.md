# Backlog — Image to 3D

> Current project state and pending work. Updated during development.
> Ultima atualizacao: 2026-06-28 (v0.3.0 closeout)

---

## Status Summary

| Category | Count |
|----------|-------|
| Completed | 21 |
| In Progress | 0 |
| Pending | 6 |

---

## In Progress

_(none)_

---

## Pending

### [ ] [MODEL | Generation presets]
**Replace raw resolution/threshold controls with safer presets**

Checklist:
- Add presets: Web Preview and Experimental
- Extend preset mapping to smoothing and decimation settings after mesh post-processing exists
- Persist selected settings during one browser session

Rationale:
- Initial Fast/Balanced/High presets exist
- Remaining work depends on mesh post-processing and preview/full output variants

### [ ] [MESH | Mesh post-processing]
**Clean and polish generated GLB before export**

Checklist:
- Add optional face decimation for preview/export variants
- Add configurable post-processing controls per preset once preview/full variants exist
- Compare smoothing settings across sample inputs

Rationale:
- Initial cleanup/smoothing now runs before GLB export
- Remaining work is focused on optimization and tuning quality tradeoffs

### [ ] [VIEWER | GPU-friendly rendering]
**Make browser manipulation cheaper and quieter on the GPU**

Checklist:
- Add an Eco/Quality toggle
- Limit rendered pixel ratio on high-DPI screens
- Prefer on-demand rendering if migrating from `<model-viewer>` to Three.js
- Pause rendering when document is hidden

Rationale:
- Current `<model-viewer>` renders a moderately heavy mesh with shadows and controls
- GPU coil whine/fan noise during interaction is expected when WebGL is driven hard

### [ ] [VIEWER | Professional presentation]
**Improve how generated models are lit, framed, and inspected**

Checklist:
- [x] Auto-frame model from bounds after load
- Use better neutral lighting/environment
- Add floor/contact shadow only when performance budget allows
- [x] Add reset camera button
- [x] Add material/solid inspection mode on current `<model-viewer>`
- Add wireframe/solid/color inspection modes if using custom Three.js
- Improve mobile layout for the viewer and controls

Rationale:
- The current viewer is functional but minimal
- Better framing and lighting can make the same GLB look less broken

### [ ] [API | Input validation and guardrails]
**Reject bad or risky uploads early**

Checklist:
- [x] Return safe job errors instead of raw tracebacks to the UI
- [x] Keep detailed tracebacks in server logs only
- [x] Add shared filename/path validation helper for preview/download routes
- [ ] Add request-level telemetry for rejected uploads

Rationale:
- Upload validation now covers the first layer of bad inputs
- Remaining work is focused on safer error surfaces and route hygiene

### [ ] [OBSERVABILITY | Generation diagnostics]
**Expose enough metrics to understand quality and performance**

Checklist:
- Log preprocessing dimensions and alpha bounding box
- [x] Log generation time per stage
- [x] Add richer `/api/status/{job_id}` diagnostics for stage timings
- Show preprocessing warnings and mesh diagnostics in a dedicated UI panel

Rationale:
- It is hard to know whether a bad result came from input crop, model inference, threshold, or viewer rendering

### [ ] [RESEARCH | Alternative mesh extraction]
**Investigate alternatives to current marching-cubes extraction**

Checklist:
- Compare current scikit-image marching cubes patch with alternatives
- Evaluate whether torchmcubes replacement affects shape quality
- Investigate Poisson/surface reconstruction post-process options
- Document visual differences with sample inputs

Rationale:
- The current patch is pragmatic for ROCm compatibility
- Quality tradeoffs should be measured instead of assumed

---

## Completed

### [x] [UX | 2026-06-28] Preprocess preview and manual mask editing
**Preview and edit the alpha mask before running full 3D generation**

- Adds `POST /api/preprocess` for model-input PNG preview without loading TripoSR
- Shows the prepared RGBA input and alpha mask in the frontend
- Adds erase/restore brush strokes with undo and clear controls
- Sends normalized `mask_edits` with generation requests
- Applies manual strokes to sanitized or original alpha before mesh generation
- Adds tests for mask edit validation and preprocessing effects

### [x] [MESH | 2026-06-28] Preview and full GLB outputs
**Generate a browser preview model separately from the full download model**

- Exports a full GLB and a `_preview.glb` for each completed job
- Uses quadric decimation for preview GLBs when available
- Keeps `output` as the full GLB for compatibility
- Adds `full_output` and `preview_output` to job status and metadata
- Updates cleanup to remove associated GLB variants, metadata and PNG previews together

### [x] [JOBS | 2026-06-28] Persistent job history
**List completed jobs from metadata sidecars after restart**

- Adds `GET /api/history`
- Reads recent completed jobs from `outputs/*.json`
- Shows a persistent history list in the frontend
- Allows reopening old preview/full outputs from history
- Adds tests for metadata-backed history

### [x] [OBSERVABILITY | 2026-06-28] Stage timing metrics
**Expose generation duration per pipeline stage**

- Tracks preprocess, model load, inference, mesh extraction, postprocess, silhouette and export timings
- Includes timings in job diagnostics, status responses and metadata
- Shows total generation time in the viewer status summary when available

### [x] [VIEWER | 2026-06-28] Viewer reset and inspection controls
**Improve basic inspection without replacing the current viewer**

- Adds reset camera control
- Reframes the model after load through `<model-viewer>` camera APIs
- Adds a matte inspection toggle for quick shape checks
- Keeps true wireframe as a future Three.js viewer task

### [x] [DEV | 2026-06-28] CPU Docker image
**Create a reproducible container for local CPU development**

- Adds `Dockerfile` based on Python 3.12 slim
- Installs CPU PyTorch plus runtime image dependencies
- Uses `IMAGE3D_DATA_DIR=/data`
- Adds `.dockerignore`
- Documents build/run commands and the `/data` cache volume

### [x] [MODEL | 2026-06-27] Flat object silhouette mode
**Preserve thin/simple object silhouettes without TripoSR volume hallucination**

- Adds `mode=auto` and `mode=silhouette` generation paths
- Extrudes the sanitized alpha mask into a thin textured GLB
- Skips model loading for silhouette mode
- Adds frontend `Auto` / `AI Volume` / `Flat Object` selector
- Preserves larger holes in preprocessing instead of filling every mask hole
- Adds `Sanitized` / `Original` model input selection
- Adds object type presets: Auto, Thin, Icon and Rounded
- Adds padding, flat-depth, alpha-cutoff and mask-bias controls
- Adds alpha-mask preview and side-by-side comparison of recent outputs
- Adds tests for mode validation and silhouette mesh generation


### [x] [JOBS | 2026-06-27] Output retention and cleanup
**Bound local output/previews/metadata growth**

- Adds startup cleanup for generated outputs
- Adds `POST /api/cleanup` for manual cleanup and dry runs
- Retains outputs by age and maximum GLB count
- Removes sidecar metadata and sanitized previews associated with removed GLBs
- Removes old orphan metadata and preview files
- Adds tests for age cleanup, count cleanup, dry-run behavior and endpoint forwarding


### [x] [DEV | 2026-06-27] Automated API test foundation
**Add fast API tests without GPU or TripoSR model loading**

- Adds pytest coverage for upload validation
- Tests generation preset resolution and advanced clamps
- Tests status responses with job metadata
- Tests preview/download filename guardrails
- Uses a fake model service so CI does not need GPU or model weights

### [x] [INFRA | 2026-06-27] Basic CI workflow
**Run syntax and API tests on GitHub Actions**

- Adds `.github/workflows/ci.yml`
- Installs Python 3.12 dependencies without PyTorch/ROCm model setup
- Runs `python -m compileall api tests`
- Runs `pytest -q`

### [x] [DEV | 2026-06-27] Repository hygiene and runtime metadata
**Remove local artifacts from source control and tighten project metadata**

- Removes tracked Python bytecode files
- Removes tracked `.deps_installed` local setup sentinel
- Ignores `.deps_installed` and `.test-data/`
- Fixes the `serve` project script to point to a Python callable
- Bumps project metadata to v0.3.0
- Adds `IMAGE3D_DATA_DIR` for isolated storage roots

### [x] [MESH | 2026-06-26] Initial mesh post-processing
**Clean generated mesh before GLB export**

- Adds `MeshPostprocessService` for dedicated mesh cleanup
- Removes degenerate faces and duplicate faces
- Removes unreferenced vertices and merges duplicate vertices
- Removes tiny disconnected mesh components
- Recalculates/fixes normals after cleanup
- Applies conservative Laplacian smoothing per preset
- Preserves the original mesh if post-processing fails
- Adds before/after mesh diagnostics to job status
- Presets now include `smoothing_iterations`

### [x] [MODEL | 2026-06-26] Initial generation presets
**Resolve generation settings from named presets**

- Adds server-side presets: Fast, Balanced, High
- Resolves preset values before submitting the job
- Keeps server-side clamps for advanced overrides
- Rejects invalid preset names with structured `400` errors
- Moves the frontend quality control from raw resolution buttons to named presets
- Moves threshold editing behind an advanced override panel
- Includes resolved preset/settings in job diagnostics

### [x] [API | 2026-06-26] Initial upload validation and parameter guardrails
**Reject bad uploads before starting generation work**

- Enforces supported content types: JPEG, PNG, WEBP
- Enforces a 15 MB upload size limit
- Decodes and verifies the uploaded image before creating a job
- Rejects extremely small images below 64px on either side
- Rejects images above 24 MP
- Clamps `resolution` to 64-384 server-side
- Clamps `mc_threshold` to 1-80 server-side
- Returns structured `400` errors for upload validation failures
- Frontend now renders structured API error messages correctly
- Sanitization now runs before model loading in the job pipeline

### [x] [QUALITY | 2026-06-26] Initial image sanitization pipeline
**Normalize input images before 3D generation**

- Added `ImagePreprocessService` for dedicated input preparation
- Applies EXIF transpose before generation
- Converts input to RGBA/RGB consistently
- Removes background and preserves alpha for crop/mask handling
- Cleans small mask noise and fills small holes
- Crops foreground from alpha bounds
- Centers foreground on a square 512x512 canvas
- Uses a stable foreground ratio and white RGB model input
- Applies light contrast/sharpness normalization
- Saves sanitized preview per job
- Adds preprocessing diagnostics to job status
- Exposes sanitized preview through `GET /api/preview/{filename}`
- Shows sanitized preview in the frontend after preprocessing
- Fixes duplicate active state in the mesh quality buttons
- Disables default auto-rotate and reduces default viewer shadow intensity
- Shows generated face count and GLB size in the viewer toolbar when available

### [x] [PORT | 2026-06-26] ROCm Linux port (v0.2.0)
**Project adapted from Windows (DirectML) to Linux (ROCm)**

- Migrated from CUDA/DirectML to PyTorch 2.12.1+rocm7.1
- Added `launch.sh` with uv-based setup
- Device detection: ROCm (`torch.version.hip`) -> CUDA -> CPU fallback
- Added `pyproject.toml` with project metadata
- Updated `requirements.txt` for Python 3.12 + ROCm compatibility
- Preserved `launch.bat` for Windows users
- Validated on AMD Radeon RX 7600 (gfx1102)
- Storage paths consolidated to `~/.local/share/image3d/`

### [x] [FEATURE | 2026-06-26] isosurface.py patch
**Replaced torchmcubes with scikit-image marching cubes**

- Full implementation of MarchingCubeHelper class
- Supports `.grid_vertices`, `.points_range`, `.__call__(level)`
- Auto-detects stale cache and re-patches
- Keep as fallback for systems without torchmcubes

### [x] [FEATURE | Initial project setup]
**Basic FastAPI project with TripoSR integration**

- FastAPI app with lifespan management
- Async job queue with threading
- Static frontend served via FastAPI

### [x] [FEATURE | Model service]
**ModelService with lazy loading and device detection**

- Downloads TripoSR source + weights automatically
- Background removal via rembg
- GLB export via trimesh
- Progress tracking per job

### [x] [FEATURE | API endpoints]
**REST API for generation**

- `POST /api/generate` — image upload
- `GET /api/status/{job_id}` — progress polling
- `GET /api/download/{filename}` — GLB download
- `GET /api/device` — device info

### [x] [FEATURE | Windows launcher]
**launch.bat for Windows users**

- pip-based dependency install
- uvicorn server start
- `.deps_installed` marker for caching
