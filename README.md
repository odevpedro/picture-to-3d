# Image to 3D

> Image to 3D model generation using TripoSR with ROCm/CUDA GPU acceleration.

[![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.12.1+rocm7.1-orange?style=flat-square)](https://pytorch.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green?style=flat-square)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey?style=flat-square)](LICENSE)

---

## Stack & Architecture

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.12 |
| GPU compute | PyTorch 2.12.1+rocm7.1 (ROCm) / CUDA fallback |
| 3D generation | TripoSR (VAST-AI-Research) |
| Mesh extraction | scikit-image marching cubes (isosurface patch) |
| Background removal | rembg |
| API framework | FastAPI + uvicorn |
| Package manager | uv (astral.sh) |
| Frontend | Static HTML (embedded) |

### Device Support

| GPU | Backend | Status |
|-----|---------|--------|
| AMD ROCm (RX 7600 gfx1102, etc.) | HIP | Active |
| NVIDIA CUDA | CUDA | Active (fallback) |
| CPU | CPU | Fallback (slow) |

Detection order: ROCm `torch.version.hip` > CUDA `torch.cuda.is_available()` > CPU.

---

## Repository Structure

```text
.
├── api/
│   ├── main.py                  # FastAPI app entry point, lifespan, frontend mount
│   ├── routers/
│   │   └── generate.py          # POST /api/generate, GET /api/status, GET /api/download
│   └── services/
│       └── model_service.py     # TripoSR loading, device detection, job queue, isosurface patch
├── frontend/
│   └── index.html               # Single-page frontend
├── launch.sh                    # Linux launcher (uv + ROCm)
├── launch.bat                   # Windows launcher (pip + DirectML)
├── pyproject.toml               # Project metadata
├── requirements.txt             # Python dependencies
├── uv.lock                      # uv lockfile
└── docs/
    ├── system-feature-flows.md  # Feature flows
    └── data-model.md            # Data model
```

---

## Local Setup (Linux - ROCm)

### Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/) package manager
- AMD GPU with ROCm 7.1 support (tested on RX 7600 gfx1102)
- ROCm 7.1 runtime installed

### Quick Start

```bash
# Clone the repository
git clone <repo-url>
cd picture-to-3d

# Make launcher executable and run
chmod +x launch.sh
./launch.sh
```

The launcher will:
1. Create a virtual environment with `uv venv --python 3.12`
2. Install PyTorch 2.12.1+rocm7.1 from the ROCm index
3. Install remaining dependencies from `requirements.txt`
4. Start the API server at `http://localhost:8080`

### Manual Setup

```bash
# Create venv and activate
uv venv --python 3.12
source .venv/bin/activate

# Install PyTorch with ROCm
uv pip install "torch==2.12.1+rocm7.1" --index-url https://download.pytorch.org/whl/rocm7.1
uv pip install "torchvision==0.27.1+rocm7.1" --index-url https://download.pytorch.org/whl/rocm7.1

# Install remaining dependencies
uv pip install -r requirements.txt

# Start the server
uv run uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```

### Windows Setup (DirectML)

```batch
:: Prerequisites: Python 3.11+, DirectML-enabled PyTorch (install manually)
python -m pip install -r requirements.txt
python -m uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```

### First Run Behavior

On first startup, the server will:
1. Download TripoSR source code from GitHub (~archive)
2. Download TripoSR pretrained weights from Hugging Face (~1.5 GB)
3. Patch the isosurface module to use scikit-image marching cubes
4. Cache everything in `~/.local/share/image3d/` for subsequent runs

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve frontend |
| GET | `/health` | Health check + active device |
| POST | `/api/generate` | Submit image for 3D generation |
| GET | `/api/status/{job_id}` | Poll generation progress |
| GET | `/api/download/{filename}` | Download generated GLB |
| GET | `/api/device` | Current device info |

### POST /api/generate

**Form parameters:**
- `image` (file, required) — Input image (JPEG, PNG, etc.)
- `resolution` (int, default 256) — Marching cubes resolution
- `mc_threshold` (float, default 25.0) — Isosurface threshold

**Response:**
```json
{ "job_id": "uuid-string" }
```

### GET /api/status/{job_id}

**Response:**
```json
{
  "job_id": "uuid-string",
  "status": "running",
  "progress": 70,
  "step": "Extracting mesh...",
  "output": null,
  "error": null
}
```

Status values: `pending` -> `running` -> `done` / `error`

### GET /api/download/{filename}

Downloads the generated GLB file. Returns `model/gltf-binary`.

---

## Storage

All persistent data is stored under `~/.local/share/image3d/`:

| Path | Purpose |
|------|---------|
| `~/.local/share/image3d/models/` | TripoSR source + weights cache |
| `~/.local/share/image3d/outputs/` | Generated GLB files |

---

## Job Processing Pipeline

1. **Submit** — Client uploads image, server creates job UUID
2. **Load model** — Loads TripoSR on ROCm/CUDA (lazy, once)
3. **Preprocess** — Background removal via rembg
4. **Generate** — TripoSR scene codes inference
5. **Extract mesh** — Marching cubes at configured resolution
6. **Export** — GLB written to outputs directory
7. **Poll** — Client polls status until done

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LD_LIBRARY_PATH` | (auto) | Injected at runtime to find torch HIP/CUDA libs |

---

## Tests

```bash
# No automated test suite yet
# Manual testing via curl:
curl -X POST http://localhost:8080/api/generate \
  -F "image=@/path/to/image.jpg" \
  -F "resolution=256" \
  -F "mc_threshold=25.0"
```

---

## Current Status

- [x] Linux ROCm support (PyTorch 2.12.1+rocm7.1)
- [x] AMD RX 7600 (gfx1102) validated
- [x] Device auto-detection (ROCm -> CUDA -> CPU)
- [x] TripoSR source auto-download + patch
- [x] isosurface.py patch (scikit-image backend)
- [x] Async job queue with progress polling
- [x] uv-based dependency management
- [x] Static frontend served by FastAPI
- [x] Windows DirectML launcher (legacy)
- [ ] CI/CD pipeline
- [ ] Automated tests
- [ ] Docker image

---

## License

MIT
