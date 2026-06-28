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
│   │   └── generate.py          # generation, preprocess, status, history routes
│   └── services/
│       ├── image_preprocess_service.py
│       ├── model_service.py     # TripoSR loading, device detection, job queue, isosurface patch
│       └── silhouette_extrude_service.py
├── frontend/
│   └── index.html               # Single-page frontend
├── tests/                       # GPU-free API/service tests
├── Dockerfile                   # CPU development image
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

### Docker Setup (CPU development)

```bash
docker build -t picture-to-3d .
docker run --rm -p 8080:8080 -v image3d-data:/data picture-to-3d
```

The Docker image is CPU-oriented for reproducible local runs. Use the native Linux setup for ROCm/CUDA acceleration.

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
| POST | `/api/preprocess` | Generate the model-input PNG preview and editable alpha mask |
| POST | `/api/generate` | Submit image for 3D generation |
| GET | `/api/status/{job_id}` | Poll generation progress |
| GET | `/api/download/{filename}` | Download generated GLB |
| GET | `/api/preview/{filename}` | Download sanitized PNG preview |
| GET | `/api/device` | Current device info |
| GET | `/api/history` | List persisted completed jobs from metadata sidecars |
| POST | `/api/cleanup` | Clean old outputs/previews/metadata |

### POST /api/generate

**Form parameters:**
- `image` (file, required) — Input image (JPEG, PNG, etc.)
- `mode` (string, default `auto`) — `auto`, `ai` for TripoSR volume, or `silhouette` for flat object extrusion
- `input_source` (string, default `sanitized`) — `sanitized` for background removal/crop, or `original` for preserved framing/alpha
- `object_type` (string, default `auto`) — UI preset hint: `auto`, `thin`, `icon`, `rounded`
- `preset` (string, default `balanced`) — One of `fast`, `balanced`, `high`
- `foreground_ratio` (float, default `0.84`) — Foreground size on sanitized square canvas
- `extrude_depth` (float, default `0.08`) — Slab depth for `silhouette` mode
- `alpha_threshold` (int, default `8`) — Alpha cutoff used to build the mask
- `mask_bias` (int, default `0`) — Negative shrinks and positive grows the sanitized mask
- `mask_edits` (JSON list, default `[]`) — Manual mask strokes: `{mode,x,y,radius}`
- `advanced` (bool, default `false`) — Whether to apply advanced overrides
- `resolution` (int, default 256) — Marching cubes resolution, used only when `advanced=true`
- `mc_threshold` (float, default 25.0) — Isosurface threshold, used only when `advanced=true`

**Response:**
```json
{ "job_id": "uuid-string", "params": {} }
```

### POST /api/preprocess

Runs the same image preparation used by generation, without loading TripoSR or exporting GLB. The frontend uses this for mask preview/editing before generation.

**Form parameters:** `image`, `input_source`, `foreground_ratio`, `alpha_threshold`, `mask_bias`, `mask_edits`

**Response:**
```json
{
  "preview": "preview_uuid_sanitized.png",
  "diagnostics": {
    "preprocess": {},
    "timings": {}
  }
}
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
  "full_output": null,
  "preview_output": null,
  "preview": "uuid_sanitized.png",
  "error": null,
  "diagnostics": {},
  "stage_timings": {},
  "created_at": 1712345678.0,
  "updated_at": 1712345680.0,
  "completed_at": null,
  "settings": {}
}
```

Status values: `pending` -> `running` -> `done` / `error`

`preview_output` is the smaller GLB used by the browser viewer. `full_output`/`output` is the full-quality GLB used by the download button.

### GET /api/history

Reads persisted job metadata from `outputs/*.json` and returns recent completed jobs, including `preview_output`, `full_output`, settings, diagnostics and stage timings.

### GET /api/download/{filename}

Downloads the generated GLB file. Returns `model/gltf-binary`.

### POST /api/cleanup

Runs the same output cleanup policy used on startup.

**Query parameters:**
- `dry_run` (bool, default `false`) — Report what would be removed without deleting files
- `max_age_days` (int, optional) — Override retention age for this cleanup run
- `max_files` (int, optional) — Override maximum retained GLB count for this cleanup run

---

## Storage

All persistent data is stored under `~/.local/share/image3d/`:

| Path | Purpose |
|------|---------|
| `~/.local/share/image3d/models/` | TripoSR source + weights cache |
| `~/.local/share/image3d/outputs/` | Generated GLB files |
| `~/.local/share/image3d/previews/` | Sanitized PNG previews |

Successful jobs write a full GLB, a preview GLB and a sidecar metadata JSON file next to the generated GLB.

Output cleanup runs on server startup and can be triggered manually through `POST /api/cleanup`.
By default, it retains generated GLBs for 14 days and keeps at most 100 GLB files. Associated
preview GLBs, metadata JSON and sanitized previews are removed with their full GLB.

---

## Job Processing Pipeline

1. **Submit** — Client uploads image, server creates job UUID
2. **Load model** — Loads TripoSR on ROCm/CUDA (lazy, once)
3. **Preprocess** — Background removal via rembg
4. **Generate** — TripoSR scene codes inference, or silhouette extrusion for `mode=silhouette`
5. **Extract mesh** — Marching cubes at configured resolution for AI mode
6. **Export** — Full GLB and web preview GLB written to outputs directory
7. **Poll** — Client polls status until done

Use `mode=silhouette` / **Flat Object** for thin front-facing objects such as blades,
logos, signs and simple icons. Use `mode=ai` / **AI Volume** for rounded objects where
hallucinated back-side volume is acceptable.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LD_LIBRARY_PATH` | (auto) | Injected at runtime to find torch HIP/CUDA libs |
| `IMAGE3D_DATA_DIR` | `~/.local/share/image3d` | Overrides model/output/preview storage root |
| `IMAGE3D_OUTPUT_RETENTION_DAYS` | `14` | Maximum age for generated outputs before cleanup |
| `IMAGE3D_MAX_OUTPUT_FILES` | `100` | Maximum number of generated GLBs retained |

---

## Tests

```bash
# Automated API tests, no GPU required
pytest -q

# Manual testing via curl:
curl -X POST http://localhost:8080/api/generate \
  -F "image=@/path/to/image.jpg" \
  -F "preset=balanced"
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
- [x] CI/CD pipeline
- [x] Automated API tests
- [x] Flat object silhouette extrusion mode
- [x] Mask/depth/padding controls and side-by-side comparison
- [x] Manual mask preview/edit strokes before generation
- [x] Persistent job history from metadata sidecars
- [x] Preview/full GLB output variants
- [x] Stage timings in job diagnostics
- [x] Docker image

---

## License

MIT
