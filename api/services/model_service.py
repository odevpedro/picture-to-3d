"""
Model service — TripoSR image-to-3D with ROCm/CUDA GPU support.
Falls back to CPU if no GPU is available.
"""
import io
import os
import time
import uuid
import threading
import ctypes
from pathlib import Path
from typing import Optional, Dict

from api.services.image_preprocess_service import image_preprocess_service
from api.services.mesh_postprocess_service import mesh_postprocess_service

# ------------------------------------------------------------------ #
# Ensure torch can find its HIP/CUDA libraries at runtime
# ------------------------------------------------------------------ #

def _setup_runtime_libs():
    try:
        import torch
        torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        ld_path = os.environ.get("LD_LIBRARY_PATH", "")
        if torch_lib not in ld_path.split(":"):
            os.environ["LD_LIBRARY_PATH"] = f"{torch_lib}:{ld_path}"
        nvrtc = os.path.join(torch_lib, "libcaffe2_nvrtc.so")
        if os.path.exists(nvrtc):
            ctypes.CDLL(nvrtc, mode=ctypes.RTLD_GLOBAL)
    except Exception:
        pass

_setup_runtime_libs()

# ------------------------------------------------------------------ #
# Device detection
# ------------------------------------------------------------------ #

def _get_device():
    import torch
    if torch.cuda.is_available():
        hip_ver = getattr(torch.version, "hip", None)
        if hip_ver:
            print(f"[ModelService] Using ROCm (AMD GPU) — HIP {hip_ver}")
            return torch.device("cuda"), f"ROCm ({hip_ver})"
        name = torch.cuda.get_device_name(0)
        print(f"[ModelService] Using CUDA (NVIDIA GPU) — {name}")
        return torch.device("cuda"), f"CUDA ({name})"
    print("[ModelService] No GPU detected, using CPU")
    return torch.device("cpu"), "CPU"


# ------------------------------------------------------------------ #
# Paths
# ------------------------------------------------------------------ #

_BASE = Path.home() / ".local" / "share" / "image3d"

MODELS_DIR  = _BASE / "models"
OUTPUTS_DIR = _BASE / "outputs"
PREVIEWS_DIR = _BASE / "previews"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ #
# isosurface.py patch — replaces torchmcubes with scikit-image
# and implements ALL attributes that system.py requires:
#   - .grid_vertices  (tensor, shape [N,3])
#   - .points_range   (tuple (-1, 1))
#   - .__call__(level) -> (v_pos tensor, t_pos_idx tensor)
# ------------------------------------------------------------------ #

_ISOSURFACE_PATCH = '''\
import torch
import numpy as np
from skimage import measure


class MarchingCubeHelper:
    def __init__(self, resolution: int):
        self.resolution  = resolution
        self.points_range = (-1.0, 1.0)

        x = torch.linspace(-1.0, 1.0, resolution)
        grid = torch.stack(
            torch.meshgrid(x, x, x, indexing="ij"), dim=-1
        ).reshape(-1, 3)
        self._grid_vertices = grid

    @property
    def grid_vertices(self) -> torch.Tensor:
        return self._grid_vertices

    def __call__(self, level: torch.Tensor):
        volume = (
            level.reshape(self.resolution, self.resolution, self.resolution)
            .detach().cpu().float().numpy()
        )
        try:
            verts, faces, _, _ = measure.marching_cubes(volume, level=0.0)
        except Exception:
            empty_v = torch.zeros((0, 3), dtype=torch.float32)
            empty_f = torch.zeros((0, 3), dtype=torch.long)
            return empty_v, empty_f

        # Normalize verts from [0, resolution-1] to [-1, 1]
        verts = verts / (self.resolution - 1) * 2.0 - 1.0
        verts_t = torch.from_numpy(verts.astype(np.float32))
        faces_t = torch.from_numpy(faces.astype(np.int64))
        return verts_t, faces_t
'''


def _patch_isosurface(src_dir: Path):
    iso_path = src_dir / "tsr" / "models" / "isosurface.py"
    if not iso_path.exists():
        return
    iso_path.write_text(_ISOSURFACE_PATCH, encoding="utf-8")
    print("[ModelService] isosurface.py patched (scikit-image backend).")


# ------------------------------------------------------------------ #
# TripoSR source downloader
# ------------------------------------------------------------------ #

def _ensure_triposr(models_dir: Path) -> Path:
    import urllib.request, zipfile

    src_dir = models_dir / "_triposr_src"
    needs_patch = True

    if (src_dir / "tsr").exists():
        # Check if already patched
        iso = src_dir / "tsr" / "models" / "isosurface.py"
        if iso.exists() and "skimage" in iso.read_text(encoding="utf-8"):
            needs_patch = False
        if not needs_patch:
            return src_dir

    if not (src_dir / "tsr").exists():
        src_dir.mkdir(parents=True, exist_ok=True)
        print("[ModelService] Downloading TripoSR source...")
        url = "https://github.com/VAST-AI-Research/TripoSR/archive/refs/heads/main.zip"
        with urllib.request.urlopen(url, timeout=180) as resp:
            data = resp.read()
        print("[ModelService] Extracting TripoSR source...")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                if not member.startswith("TripoSR-main/tsr/"):
                    continue
                rel    = member[len("TripoSR-main/"):]
                target = src_dir / rel
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(member))

    _patch_isosurface(src_dir)
    print("[ModelService] TripoSR source ready.")
    return src_dir


# ------------------------------------------------------------------ #
# Weights downloader
# ------------------------------------------------------------------ #

def _ensure_weights(models_dir: Path) -> Path:
    weights_dir = models_dir / "triposr"
    if (weights_dir / "model.ckpt").exists() and (weights_dir / "config.yaml").exists():
        return weights_dir

    from huggingface_hub import hf_hub_download
    weights_dir.mkdir(parents=True, exist_ok=True)
    print("[ModelService] Downloading TripoSR weights (~1.5 GB)...")
    hf_hub_download(repo_id="stabilityai/TripoSR", filename="model.ckpt",  local_dir=str(weights_dir))
    hf_hub_download(repo_id="stabilityai/TripoSR", filename="config.yaml", local_dir=str(weights_dir))
    print("[ModelService] Weights downloaded.")
    return weights_dir


# ------------------------------------------------------------------ #
# Job tracking
# ------------------------------------------------------------------ #

class Job:
    def __init__(self, job_id: str):
        self.job_id   = job_id
        self.status   = "pending"
        self.progress = 0
        self.step     = ""
        self.output   = None
        self.preview  = None
        self.error    = None
        self.diagnostics = {}


# ------------------------------------------------------------------ #
# ModelService
# ------------------------------------------------------------------ #

class ModelService:
    def __init__(self):
        self._model      = None
        self._lock       = threading.Lock()
        self._jobs: Dict[str, Job] = {}
        self.device      = None
        self.device_name = "not loaded"

    def _load(self):
        if self._model is not None:
            return

        import sys, torch

        self.device, self.device_name = _get_device()

        src_dir     = _ensure_triposr(MODELS_DIR)
        weights_dir = _ensure_weights(MODELS_DIR)

        src_path = str(src_dir)
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        # Always re-patch in case of stale cache
        _patch_isosurface(src_dir)

        # Force reload of tsr.models.isosurface so patch takes effect
        import importlib, sys as _sys
        for mod_name in list(_sys.modules.keys()):
            if "tsr" in mod_name:
                del _sys.modules[mod_name]

        from tsr.system import TSR

        print(f"[ModelService] Loading TripoSR on {self.device_name}...")
        model = TSR.from_pretrained(
            str(weights_dir),
            config_name="config.yaml",
            weight_name="model.ckpt",
        )
        model.renderer.set_chunk_size(131072)

        try:
            model = model.to(self.device)
        except Exception as e:
            print(f"[ModelService] .to({self.device_name}) failed: {e} — falling back to CPU")
            self.device      = torch.device("cpu")
            self.device_name = "CPU (fallback)"
            model = model.to(self.device)

        model.eval()
        self._model = model
        print(f"[ModelService] Model ready on {self.device_name}.")

    def unload(self):
        self._model = None

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def submit(self, image_bytes: bytes, params: dict) -> str:
        job_id = str(uuid.uuid4())
        job    = Job(job_id)
        job.diagnostics["request"] = params
        self._jobs[job_id] = job
        threading.Thread(target=self._run, args=(job, image_bytes, params), daemon=True).start()
        return job_id

    def _run(self, job: Job, image_bytes: bytes, params: dict):
        import torch

        job.status = "running"
        try:
            job.progress, job.step = 2,  "Sanitizing image..."
            preprocess_result = self._preprocess(image_bytes, job.job_id)
            image = preprocess_result.image
            job.preview = preprocess_result.preview_filename
            job.diagnostics["preprocess"] = preprocess_result.diagnostics

            job.progress, job.step = 10, "Loading model..."
            with self._lock:
                self._load()

            job.progress, job.step = 15, "Generating 3D shape..."
            resolution   = int(params.get("resolution",    256))
            mc_threshold = float(params.get("mc_threshold", 25.0))

            with torch.no_grad():
                scene_codes = self._model(image, device=self.device)

            job.progress, job.step = 70, "Extracting mesh..."
            mesh = self._model.extract_mesh(
                scene_codes,
                True,                   # has_vertex_color
                resolution=resolution,
                threshold=mc_threshold,
            )[0]

            job.progress, job.step = 84, "Cleaning mesh..."
            mesh, mesh_diagnostics = self._postprocess_mesh(mesh, params)
            job.diagnostics["mesh"] = mesh_diagnostics

            job.progress, job.step = 92, "Exporting GLB..."
            filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.glb"
            out_path = OUTPUTS_DIR / filename
            mesh.export(str(out_path))
            job.diagnostics["output"] = {
                "filename": filename,
                "file_size_bytes": out_path.stat().st_size,
                "resolution": resolution,
                "mc_threshold": mc_threshold,
            }

            job.output   = filename
            job.progress = 100
            job.step     = "Done"
            job.status   = "done"

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"[ModelService] ERROR: {exc}\n{tb}")
            job.status = "error"
            job.error  = tb.strip()

    def _preprocess(self, image_bytes: bytes, job_id: str):
        return image_preprocess_service.prepare(
            image_bytes,
            preview_dir=PREVIEWS_DIR,
            preview_stem=job_id,
        )

    def _mesh_diagnostics(self, mesh) -> dict:
        vertices = getattr(mesh, "vertices", [])
        faces = getattr(mesh, "faces", [])
        bounds = getattr(mesh, "bounds", None)
        extents = getattr(mesh, "extents", None)
        return {
            "vertices": len(vertices),
            "faces": len(faces),
            "bounds": bounds.tolist() if bounds is not None else None,
            "extents": extents.tolist() if extents is not None else None,
        }

    def _postprocess_mesh(self, mesh, params: dict):
        try:
            result = mesh_postprocess_service.process(
                mesh,
                smoothing_iterations=int(params.get("smoothing_iterations", 2)),
            )
            diagnostics = self._mesh_diagnostics(result.mesh)
            diagnostics["postprocess"] = result.diagnostics
            return result.mesh, diagnostics
        except Exception as exc:
            diagnostics = self._mesh_diagnostics(mesh)
            diagnostics["postprocess"] = {
                "error": str(exc),
                "applied": False,
            }
            return mesh, diagnostics


model_service = ModelService()
