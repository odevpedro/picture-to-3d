# Backlog — Image to 3D

> Current project state and pending work. Updated during development.
> Ultima atualizacao: 2026-06-26

---

## Status Summary

| Category | Count |
|----------|-------|
| Completed | 6 |
| In Progress | 0 |
| Pending | 4 |

---

## In Progress

_(none)_

---

## Pending

### [ ] [INFRA | CI/CD pipeline]
**Set up automated CI for Linux ROCm build**

- GitHub Actions workflow for dependency installation
- Basic health check test on startup

### [ ] [DEV | Automated tests]
**Add test suite for API endpoints**

- Test image upload, status polling, download
- Mock model service for CI without GPU

### [ ] [DEV | Docker image]
**Create Dockerfile for reproducible builds**

- ROCm base image from AMD
- Pre-cached weights layer

### [ ] [DEV | Alternative mesh extraction]
**Investigate alternatives to marching cubes**

- Neural mesh extraction
- Poisson surface reconstruction

---

## Completed

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
