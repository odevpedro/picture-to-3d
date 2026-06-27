# Backlog — Image to 3D

> Current project state and pending work. Updated during development.
> Ultima atualizacao: 2026-06-26 (v0.3.0 planning)

---

## Status Summary

| Category | Count |
|----------|-------|
| Completed | 10 |
| In Progress | 0 |
| Pending | 12 |

---

## In Progress

_(none)_

---

## Pending

### [ ] [UX | Sanitized image preview]
**Show the user the actual image that will be sent to the 3D model**

Checklist:
- Add preprocessing-only endpoint before running full 3D generation
- Show warnings in a dedicated UI area instead of status text only
- Allow user to adjust padding/crop preset before generation
- Add "generate from this sanitized input" flow if preprocessing becomes async

Rationale:
- Users need to see whether the model received a clean object or a bad crop
- This makes image quality problems explainable instead of mysterious

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

### [ ] [MESH | Dual output variants]
**Generate a web-optimized preview model and a full-quality download model**

Checklist:
- Export `{job_id}_preview.glb` with lower face count
- Export `{job_id}_full.glb` with higher quality settings
- Show preview GLB in the browser by default
- Keep the full GLB available from the download button
- Include both filenames in `/api/status/{job_id}`

Rationale:
- The browser viewer does not need the full mesh
- Smaller preview models reduce GPU load and make interaction smoother

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
- Auto-frame model from bounds after load
- Use better neutral lighting/environment
- Add floor/contact shadow only when performance budget allows
- Add reset camera button
- Add wireframe/solid/color inspection modes if using custom Three.js
- Improve mobile layout for the viewer and controls

Rationale:
- The current viewer is functional but minimal
- Better framing and lighting can make the same GLB look less broken

### [ ] [API | Input validation and guardrails]
**Reject bad or risky uploads early**

Checklist:
- Return structured errors instead of raw tracebacks to the UI
- Keep detailed tracebacks in server logs only
- Add shared filename/path validation helper for preview/download routes
- Add request-level telemetry for rejected uploads

Rationale:
- Upload validation now covers the first layer of bad inputs
- Remaining work is focused on safer error surfaces and route hygiene

### [ ] [JOBS | Lifecycle and cleanup]
**Make local job/output storage more predictable**

Checklist:
- Store job metadata next to generated files
- Add output retention policy
- Add cleanup endpoint or startup cleanup task
- Avoid unbounded growth of in-memory `_jobs`
- Include timestamps and settings in job status
- Add a simple job history view in the UI

Rationale:
- Jobs are currently in memory and outputs accumulate in `~/.local/share/image3d/outputs/`
- This is acceptable for early local use but not for prolonged use

### [ ] [OBSERVABILITY | Generation diagnostics]
**Expose enough metrics to understand quality and performance**

Checklist:
- Log preprocessing dimensions and alpha bounding box
- Log generation time per stage
- Add richer `/api/status/{job_id}` diagnostics for stage timings
- Show preprocessing warnings and mesh diagnostics in a dedicated UI panel

Rationale:
- It is hard to know whether a bad result came from input crop, model inference, threshold, or viewer rendering

### [ ] [DEV | Automated tests]
**Add test suite for API endpoints**

Checklist:
- Test image upload validation
- Test status polling
- Test download path traversal protection
- Mock model service for CI without GPU
- Add tests for image sanitization once implemented

### [ ] [INFRA | CI/CD pipeline]
**Set up automated CI for Linux build without requiring GPU**

Checklist:
- GitHub Actions workflow for dependency installation
- Run API tests with mocked model service
- Basic health check test on startup
- Validate formatting/imports

### [ ] [DEV | Docker image]
**Create Dockerfile for reproducible local builds**

Checklist:
- ROCm-oriented Linux image option
- CPU-only development image option
- Pre-cache Python dependencies
- Document model weights cache volume

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
