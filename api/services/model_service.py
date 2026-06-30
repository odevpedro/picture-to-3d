"""
Model service — TripoSR image-to-3D with ROCm/CUDA GPU support.
Falls back to CPU if no GPU is available.
"""
import io
import json
import os
import queue
import time
import uuid
import threading
import ctypes
import hashlib
import shutil
import sys
from pathlib import Path
from typing import Optional, Dict

from api.services.image_preprocess_service import image_preprocess_service
from api.services.mesh_postprocess_service import mesh_postprocess_service
from api.services.silhouette_extrude_service import silhouette_extrude_service

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

_BASE = Path(os.environ.get("IMAGE3D_DATA_DIR", Path.home() / ".local" / "share" / "image3d"))

MODELS_DIR  = _BASE / "models"
OUTPUTS_DIR = _BASE / "outputs"
PREVIEWS_DIR = _BASE / "previews"
DEFAULT_OUTPUT_RETENTION_DAYS = 14
DEFAULT_MAX_OUTPUT_FILES = 100
DEFAULT_WORKER_COUNT = 1
DEFAULT_MAX_QUEUE_SIZE = 4
DEFAULT_JOB_TIMEOUT_SECONDS = 30 * 60
DEFAULT_TRIPOSR_SOURCE_REF = "107cefdc244c39106fa830359024f6a2f1c78871"
TRIPOSR_REQUIRED_FILES = (
    "tsr/system.py",
    "tsr/models/isosurface.py",
)
MIN_MODEL_CKPT_BYTES = 100 * 1024 * 1024


def _ensure_storage_dirs():
    for path in (MODELS_DIR, OUTPUTS_DIR, PREVIEWS_DIR):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[ModelService] Could not create storage directory {path}: {exc}")


_ensure_storage_dirs()


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _offline_mode() -> bool:
    return _env_flag("IMAGE3D_OFFLINE")


class JobQueueFull(Exception):
    def __init__(self, max_queue_size: int):
        self.max_queue_size = max_queue_size
        super().__init__(f"Generation queue is full ({max_queue_size})")


class JobCancelled(Exception):
    pass


class JobTimeout(Exception):
    pass


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


def _triposr_ref() -> str:
    return os.environ.get("TRIPOSR_SOURCE_REF", DEFAULT_TRIPOSR_SOURCE_REF).strip()


def _triposr_source_url(source_ref: str) -> str:
    return f"https://github.com/VAST-AI-Research/TripoSR/archive/{source_ref}.zip"


def _source_ref_marker(src_dir: Path) -> Path:
    return src_dir / ".source_ref"


def _source_ref_matches(src_dir: Path, source_ref: str) -> bool:
    try:
        return _source_ref_marker(src_dir).read_text(encoding="utf-8").strip() == source_ref
    except OSError:
        return False


def _triposr_source_valid(src_dir: Path) -> bool:
    return all((src_dir / relative).is_file() for relative in TRIPOSR_REQUIRED_FILES)


# ------------------------------------------------------------------ #
# TripoSR source downloader
# ------------------------------------------------------------------ #

def _ensure_triposr(models_dir: Path) -> Path:
    import urllib.request, zipfile

    source_ref = _triposr_ref()
    src_dir = models_dir / "_triposr_src"

    if _triposr_source_valid(src_dir) and _source_ref_matches(src_dir, source_ref):
        iso = src_dir / "tsr" / "models" / "isosurface.py"
        if iso.exists() and "skimage" in iso.read_text(encoding="utf-8"):
            return src_dir
        _patch_isosurface(src_dir)
        return src_dir

    if _offline_mode():
        raise RuntimeError(
            "TripoSR source cache is missing or invalid and IMAGE3D_OFFLINE is enabled."
        )

    tmp_dir = models_dir / f"_triposr_src.tmp-{uuid.uuid4().hex[:8]}"
    try:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        print(f"[ModelService] Downloading TripoSR source at {source_ref}...")
        url = _triposr_source_url(source_ref)
        with urllib.request.urlopen(url, timeout=180) as resp:
            data = resp.read()
        print("[ModelService] Extracting TripoSR source...")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                parts = member.split("/", 1)
                if len(parts) != 2:
                    continue
                rel = parts[1]
                if not rel.startswith("tsr/"):
                    continue
                target = tmp_dir / rel
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(member))

        if not _triposr_source_valid(tmp_dir):
            missing = [
                relative
                for relative in TRIPOSR_REQUIRED_FILES
                if not (tmp_dir / relative).is_file()
            ]
            raise RuntimeError(f"Downloaded TripoSR source is incomplete: {missing}")

        _source_ref_marker(tmp_dir).write_text(source_ref, encoding="utf-8")
        _patch_isosurface(tmp_dir)
        if src_dir.exists():
            shutil.rmtree(src_dir)
        tmp_dir.replace(src_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    print("[ModelService] TripoSR source ready.")
    return src_dir


# ------------------------------------------------------------------ #
# Weights downloader
# ------------------------------------------------------------------ #

def _ensure_weights(models_dir: Path) -> Path:
    weights_dir = models_dir / "triposr"
    if _weights_cache_valid(weights_dir):
        return weights_dir

    if _offline_mode():
        raise RuntimeError(
            "TripoSR weights cache is missing or invalid and IMAGE3D_OFFLINE is enabled."
        )

    from huggingface_hub import hf_hub_download
    weights_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("model.ckpt", "config.yaml"):
        path = weights_dir / filename
        if path.exists():
            path.unlink()
    print("[ModelService] Downloading TripoSR weights (~1.5 GB)...")
    hf_hub_download(repo_id="stabilityai/TripoSR", filename="model.ckpt",  local_dir=str(weights_dir))
    hf_hub_download(repo_id="stabilityai/TripoSR", filename="config.yaml", local_dir=str(weights_dir))
    if not _weights_cache_valid(weights_dir):
        raise RuntimeError("Downloaded TripoSR weights are incomplete.")
    print("[ModelService] Weights downloaded.")
    return weights_dir


def _weights_cache_valid(weights_dir: Path) -> bool:
    ckpt = weights_dir / "model.ckpt"
    config = weights_dir / "config.yaml"
    try:
        return (
            ckpt.is_file()
            and ckpt.stat().st_size >= MIN_MODEL_CKPT_BYTES
            and config.is_file()
            and config.stat().st_size > 0
        )
    except OSError:
        return False


# ------------------------------------------------------------------ #
# Job tracking
# ------------------------------------------------------------------ #

class Job:
    def __init__(
        self,
        job_id: str,
        settings: dict | None = None,
        *,
        timeout_seconds: int | None = None,
        submission_key: str | None = None,
    ):
        now = time.time()
        self.job_id   = job_id
        self.status   = "pending"
        self.progress = 0
        self.step     = "Queued"
        self.output   = None
        self.full_output = None
        self.preview_output = None
        self.preview  = None
        self.error    = None
        self.settings = settings or {}
        self.diagnostics = {}
        self.stage_timings = {}
        self.created_at = now
        self.updated_at = now
        self.completed_at = None
        self.queued_at = now
        self.queue_position = None
        self.started_at = None
        self.started_monotonic = None
        self.timeout_seconds = timeout_seconds
        self.cancel_requested = False
        self.cancelled_at = None
        self.submission_key = submission_key

    def touch(self):
        self.updated_at = time.time()


# ------------------------------------------------------------------ #
# ModelService
# ------------------------------------------------------------------ #

class ModelService:
    def __init__(
        self,
        *,
        worker_count: int | None = None,
        max_queue_size: int | None = None,
        job_timeout_seconds: int | None = None,
        start_workers: bool = True,
    ):
        self._model      = None
        self._lock       = threading.Lock()
        self._jobs_lock  = threading.Lock()
        self._jobs: Dict[str, Job] = {}
        self._max_jobs   = 100
        self.worker_count = worker_count or _env_positive_int(
            "IMAGE3D_WORKERS",
            DEFAULT_WORKER_COUNT,
        )
        self.max_queue_size = max_queue_size or _env_positive_int(
            "IMAGE3D_MAX_QUEUE_SIZE",
            DEFAULT_MAX_QUEUE_SIZE,
        )
        self.job_timeout_seconds = job_timeout_seconds or _env_positive_int(
            "IMAGE3D_JOB_TIMEOUT_SECONDS",
            DEFAULT_JOB_TIMEOUT_SECONDS,
        )
        self._queue: queue.Queue[tuple[str, bytes, dict]] = queue.Queue(
            maxsize=self.max_queue_size
        )
        self._workers: list[threading.Thread] = []
        self._submission_keys: dict[str, str] = {}
        self.output_retention_days = _env_positive_int(
            "IMAGE3D_OUTPUT_RETENTION_DAYS",
            DEFAULT_OUTPUT_RETENTION_DAYS,
        )
        self.max_output_files = _env_positive_int(
            "IMAGE3D_MAX_OUTPUT_FILES",
            DEFAULT_MAX_OUTPUT_FILES,
        )
        self.device      = None
        self.device_name = "not loaded"
        if start_workers:
            self._start_workers()

    def _load(self):
        if self._model is not None:
            return

        _setup_runtime_libs()

        import torch

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
        with self._jobs_lock:
            self._refresh_queue_positions_locked()
            return self._jobs.get(job_id)

    def submit(self, image_bytes: bytes, params: dict) -> str:
        submission_key = self._submission_key(image_bytes, params)
        with self._jobs_lock:
            existing_id = self._active_duplicate_job_id_locked(submission_key)
            if existing_id:
                existing = self._jobs[existing_id]
                duplicate_count = existing.diagnostics.get("duplicate_submissions", 0) + 1
                existing.diagnostics["duplicate_submissions"] = duplicate_count
                existing.touch()
                return existing_id

        job_id = str(uuid.uuid4())
        job    = Job(
            job_id,
            settings=params,
            timeout_seconds=self.job_timeout_seconds,
            submission_key=submission_key,
        )
        job.diagnostics["request"] = params
        with self._jobs_lock:
            self._prune_jobs_locked()
            self._jobs[job_id] = job
            self._submission_keys[submission_key] = job_id
        try:
            self._queue.put_nowait((job_id, image_bytes, params))
        except queue.Full as exc:
            with self._jobs_lock:
                self._jobs.pop(job_id, None)
                if self._submission_keys.get(submission_key) == job_id:
                    self._submission_keys.pop(submission_key, None)
            raise JobQueueFull(self.max_queue_size) from exc
        with self._jobs_lock:
            self._refresh_queue_positions_locked()
        return job_id

    def cancel_job(self, job_id: str) -> Optional[Job]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in {"done", "error", "cancelled"}:
                return job
            job.cancel_requested = True
            if job.status == "pending":
                self._remove_queued_job_locked(job_id)
                job.status = "cancelled"
                job.progress = 0
                job.step = "Cancelled"
                job.error = None
                job.cancelled_at = time.time()
                job.completed_at = job.cancelled_at
                job.queue_position = None
                self._drop_submission_key_locked(job)
            else:
                job.step = "Cancelling..."
            job.touch()
            self._refresh_queue_positions_locked()
            return job

    def prepare_preview(self, image_bytes: bytes, params: dict) -> dict:
        preview_id = f"preview_{uuid.uuid4().hex[:12]}"
        started = time.perf_counter()
        result = self._preprocess(
            image_bytes,
            preview_id,
            input_source=str(params.get("input_source", "sanitized")),
            foreground_ratio=float(params.get("foreground_ratio", 0.84)),
            alpha_threshold=int(params.get("alpha_threshold", 8)),
            mask_bias=int(params.get("mask_bias", 0)),
            mask_edits=params.get("mask_edits") or [],
        )
        return {
            "preview": result.preview_filename,
            "diagnostics": {
                "preprocess": result.diagnostics,
                "timings": {
                    "preprocess": {
                        "duration_seconds": round(time.perf_counter() - started, 3),
                    },
                },
            },
        }

    def preflight(self) -> dict:
        source_ref = _triposr_ref()
        source_ready = _triposr_source_valid(MODELS_DIR / "_triposr_src") and _source_ref_matches(
            MODELS_DIR / "_triposr_src",
            source_ref,
        )
        weights_ready = _weights_cache_valid(MODELS_DIR / "triposr")
        return {
            "python": {
                "version": sys.version.split()[0],
                "executable": sys.executable,
            },
            "storage": self._storage_preflight(),
            "torch": self._torch_preflight(),
            "cache": {
                "triposr_source": {
                    "ready": source_ready,
                    "ref": source_ref,
                    "path": str(MODELS_DIR / "_triposr_src"),
                },
                "triposr_weights": {
                    "ready": weights_ready,
                    "path": str(MODELS_DIR / "triposr"),
                    "minimum_model_bytes": MIN_MODEL_CKPT_BYTES,
                },
            },
            "setup": {
                "offline": _offline_mode(),
                "network_required": not (source_ready and weights_ready),
                "warmup_command": "uv run --no-sync python -m api.main --warmup",
            },
            "queue": {
                "worker_count": self.worker_count,
                "max_queue_size": self.max_queue_size,
                "job_timeout_seconds": self.job_timeout_seconds,
            },
        }

    def warmup(self) -> dict:
        self._load()
        return self.preflight()

    def _storage_preflight(self) -> dict:
        _ensure_storage_dirs()
        result = {
            "base_dir": str(_BASE),
            "models_dir": str(MODELS_DIR),
            "outputs_dir": str(OUTPUTS_DIR),
            "previews_dir": str(PREVIEWS_DIR),
            "writable": False,
            "free_bytes": None,
            "error": None,
        }
        probe = _BASE / ".image3d_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            result["writable"] = True
            result["free_bytes"] = shutil.disk_usage(_BASE).free
        except OSError as exc:
            result["error"] = str(exc)
        return result

    def _torch_preflight(self) -> dict:
        _setup_runtime_libs()
        try:
            import torch
        except Exception as exc:
            return {
                "installed": False,
                "error": str(exc),
            }

        cuda_available = bool(torch.cuda.is_available())
        hip_version = getattr(torch.version, "hip", None)
        info = {
            "installed": True,
            "version": getattr(torch, "__version__", None),
            "cuda_available": cuda_available,
            "hip_version": hip_version,
            "backend": "cpu",
            "devices": [],
        }
        if cuda_available:
            info["backend"] = "rocm" if hip_version else "cuda"
            for index in range(torch.cuda.device_count()):
                try:
                    props = torch.cuda.get_device_properties(index)
                    info["devices"].append(
                        {
                            "index": index,
                            "name": props.name,
                            "total_memory_bytes": props.total_memory,
                        }
                    )
                except Exception as exc:
                    info["devices"].append({"index": index, "error": str(exc)})
        return info

    def _start_workers(self):
        for index in range(self.worker_count):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"image3d-worker-{index + 1}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def _worker_loop(self):
        while True:
            job_id, image_bytes, params = self._queue.get()
            try:
                with self._jobs_lock:
                    job = self._jobs.get(job_id)
                    if job is None:
                        self._refresh_queue_positions_locked()
                        continue
                    if job.cancel_requested or job.status == "cancelled":
                        self._refresh_queue_positions_locked()
                        continue
                    job.status = "running"
                    job.started_at = time.time()
                    job.started_monotonic = time.monotonic()
                    job.queue_position = None
                    job.touch()
                    self._refresh_queue_positions_locked()
                self._run(job, image_bytes, params)
            finally:
                with self._jobs_lock:
                    self._expire_submission_keys_locked()
                    self._refresh_queue_positions_locked()
                self._queue.task_done()

    def _run(self, job: Job, image_bytes: bytes, params: dict):
        job.status = "running"
        if job.started_at is None:
            job.started_at = time.time()
        if job.started_monotonic is None:
            job.started_monotonic = time.monotonic()
        job.touch()
        try:
            import torch

            stage_started = self._start_stage(job, "preprocess", 2, "Sanitizing image...")
            preprocess_result = self._preprocess(
                image_bytes,
                job.job_id,
                input_source=str(params.get("input_source", "sanitized")),
                foreground_ratio=float(params.get("foreground_ratio", 0.84)),
                alpha_threshold=int(params.get("alpha_threshold", 8)),
                mask_bias=int(params.get("mask_bias", 0)),
                mask_edits=params.get("mask_edits") or [],
            )
            self._finish_stage(job, "preprocess", stage_started)
            image = preprocess_result.image
            rgba_image = preprocess_result.rgba
            job.preview = preprocess_result.preview_filename
            job.diagnostics["preprocess"] = preprocess_result.diagnostics

            mode = self._effective_generation_mode(params, preprocess_result.diagnostics)
            params["effective_mode"] = mode
            job.diagnostics["request"] = params
            if mode == "silhouette":
                stage_started = self._start_stage(job, "silhouette", 35, "Extruding silhouette...")
                result = silhouette_extrude_service.process(
                    rgba_image,
                    preprocess_result.alpha,
                    depth_scale=float(params.get("extrude_depth", 0.08)),
                )
                self._finish_stage(job, "silhouette", stage_started)
                job.diagnostics["silhouette"] = result.diagnostics
                self._export_mesh(job, result.mesh, params)
                return

            stage_started = self._start_stage(job, "load_model", 10, "Loading model...")
            with self._lock:
                self._load()
            self._finish_stage(job, "load_model", stage_started)

            stage_started = self._start_stage(job, "inference", 15, "Generating 3D shape...")
            resolution   = int(params.get("resolution",    256))
            mc_threshold = float(params.get("mc_threshold", 25.0))

            with torch.no_grad():
                scene_codes = self._model(image, device=self.device)
            self._finish_stage(job, "inference", stage_started)

            stage_started = self._start_stage(job, "extract_mesh", 70, "Extracting mesh...")
            mesh = self._model.extract_mesh(
                scene_codes,
                True,                   # has_vertex_color
                resolution=resolution,
                threshold=mc_threshold,
            )[0]
            self._finish_stage(job, "extract_mesh", stage_started)

            stage_started = self._start_stage(job, "postprocess_mesh", 84, "Cleaning mesh...")
            mesh, mesh_diagnostics = self._postprocess_mesh(mesh, params)
            self._finish_stage(job, "postprocess_mesh", stage_started)
            job.diagnostics["mesh"] = mesh_diagnostics

            self._export_mesh(job, mesh, params)

        except JobCancelled:
            self._mark_cancelled(job)
        except JobTimeout:
            self._mark_timed_out(job)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"[ModelService] ERROR: {exc}\n{tb}")
            job.status = "error"
            job.error  = "Generation failed. See server logs for details."
            job.diagnostics["error"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
            job.diagnostics["timings"] = job.stage_timings
            job.completed_at = time.time()
            job.touch()
        finally:
            with self._jobs_lock:
                self._drop_submission_key_locked(job)

    def _preprocess(
        self,
        image_bytes: bytes,
        job_id: str,
        *,
        input_source: str,
        foreground_ratio: float,
        alpha_threshold: int,
        mask_bias: int,
        mask_edits: list[dict],
    ):
        return image_preprocess_service.prepare(
            image_bytes,
            input_source=input_source,
            foreground_ratio=foreground_ratio,
            alpha_threshold=alpha_threshold,
            mask_bias=mask_bias,
            mask_edits=mask_edits,
            preview_dir=PREVIEWS_DIR,
            preview_stem=job_id,
        )

    def _start_stage(self, job: Job, name: str, progress: int, step: str) -> float:
        self._raise_if_cancelled_or_timed_out(job)
        job.progress, job.step = progress, step
        job.stage_timings.setdefault(name, {"started_at": time.time()})
        job.diagnostics["timings"] = job.stage_timings
        job.touch()
        return time.perf_counter()

    def _finish_stage(self, job: Job, name: str, started: float):
        elapsed = max(0.0, time.perf_counter() - started)
        timing = job.stage_timings.setdefault(name, {})
        timing["duration_seconds"] = round(elapsed, 3)
        timing["completed_at"] = time.time()
        job.diagnostics["timings"] = job.stage_timings
        job.touch()
        self._raise_if_cancelled_or_timed_out(job)

    def _raise_if_cancelled_or_timed_out(self, job: Job):
        if job.cancel_requested:
            raise JobCancelled()
        timeout_seconds = job.timeout_seconds
        started = job.started_monotonic
        if timeout_seconds and started is not None:
            if time.monotonic() - started > timeout_seconds:
                raise JobTimeout()

    def _mark_cancelled(self, job: Job):
        job.status = "cancelled"
        job.error = None
        job.step = "Cancelled"
        job.cancel_requested = True
        job.cancelled_at = time.time()
        job.completed_at = job.cancelled_at
        job.diagnostics["timings"] = job.stage_timings
        job.touch()

    def _mark_timed_out(self, job: Job):
        job.status = "error"
        job.error = "Generation timed out."
        job.step = "Timed out"
        job.diagnostics["error"] = {
            "type": "JobTimeout",
            "message": f"Generation exceeded {job.timeout_seconds} seconds",
        }
        job.diagnostics["timings"] = job.stage_timings
        job.completed_at = time.time()
        job.touch()

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

    def _export_mesh(self, job: Job, mesh, params: dict):
        stage_started = self._start_stage(job, "export", 92, "Exporting GLB...")
        stem = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        filename = f"{stem}.glb"
        preview_filename = f"{stem}_preview.glb"
        out_path = OUTPUTS_DIR / filename
        preview_path = OUTPUTS_DIR / preview_filename

        mesh.export(str(out_path))
        preview_mesh, preview_diagnostics = self._make_preview_mesh(mesh)
        preview_mesh.export(str(preview_path))
        self._finish_stage(job, "export", stage_started)

        output_diagnostics = {
            "filename": filename,
            "full_filename": filename,
            "preview_filename": preview_filename,
            "file_size_bytes": out_path.stat().st_size,
            "preview_file_size_bytes": preview_path.stat().st_size,
            "resolution": params.get("resolution"),
            "mc_threshold": params.get("mc_threshold"),
            "mode": params.get("mode", "auto"),
            "effective_mode": params.get("effective_mode", params.get("mode", "auto")),
            "preview_mesh": preview_diagnostics,
        }
        job.diagnostics["output"] = {
            **output_diagnostics,
        }
        job.diagnostics.setdefault("mesh", self._mesh_diagnostics(mesh))
        job.diagnostics["timings"] = job.stage_timings

        job.output = filename
        job.full_output = filename
        job.preview_output = preview_filename
        job.progress = 100
        job.step = "Done"
        job.status = "done"
        job.completed_at = time.time()
        job.touch()
        self._write_job_metadata(job)

    def _make_preview_mesh(self, mesh, *, max_faces: int = 50_000):
        faces = getattr(mesh, "faces", [])
        face_count = len(faces)
        diagnostics = {
            "source_faces": int(face_count),
            "target_faces": int(max_faces),
            "simplified": False,
            "faces": int(face_count),
        }
        if face_count <= max_faces:
            return mesh.copy(), diagnostics

        try:
            preview_mesh = mesh.simplify_quadric_decimation(
                face_count=max_faces,
                aggression=7,
            )
            diagnostics["simplified"] = True
            diagnostics["faces"] = int(len(getattr(preview_mesh, "faces", [])))
            return preview_mesh, diagnostics
        except Exception as exc:
            diagnostics["error"] = str(exc)
            return mesh.copy(), diagnostics

    def _write_job_metadata(self, job: Job):
        if not job.output:
            return

        metadata_path = OUTPUTS_DIR / f"{Path(job.output).stem}.json"
        metadata = {
            "job_id": job.job_id,
            "status": job.status,
            "output": job.output,
            "full_output": job.full_output or job.output,
            "preview_output": job.preview_output or job.output,
            "outputs": {
                "full": job.full_output or job.output,
                "preview": job.preview_output or job.output,
            },
            "preview": job.preview,
            "settings": job.settings,
            "diagnostics": job.diagnostics,
            "stage_timings": job.stage_timings,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "completed_at": job.completed_at,
        }
        try:
            metadata_path.write_text(
                json.dumps(metadata, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"[ModelService] Could not write job metadata: {exc}")

    def _effective_generation_mode(self, params: dict, preprocess_diagnostics: dict) -> str:
        requested = str(params.get("mode", "auto")).strip().lower()
        if requested in {"ai", "silhouette"}:
            return requested

        object_type = str(params.get("object_type", "auto")).strip().lower()
        if object_type in {"thin", "icon"}:
            return "silhouette"
        if object_type == "rounded":
            return "ai"

        if preprocess_diagnostics.get("used_source_alpha"):
            return "silhouette"

        mask_area_ratio = float(preprocess_diagnostics.get("mask_area_ratio") or 0)
        foreground_size = preprocess_diagnostics.get("foreground_size") or [0, 0]
        width, height = foreground_size
        long_side = max(width, height, 1)
        short_side = max(min(width, height), 1)
        aspect_ratio = long_side / short_side

        if mask_area_ratio <= 0.12 or aspect_ratio >= 2.8:
            return "silhouette"
        return "ai"

    def cleanup_outputs(
        self,
        *,
        dry_run: bool = False,
        max_age_days: int | None = None,
        max_files: int | None = None,
    ) -> dict:
        effective_max_age_days = self._normalize_positive_int(
            max_age_days,
            self.output_retention_days,
        )
        effective_max_files = self._normalize_positive_int(
            max_files,
            self.max_output_files,
        )

        outputs = self._list_files(OUTPUTS_DIR, "*.glb")
        metadata_files = self._list_files(OUTPUTS_DIR, "*.json")
        preview_files = self._list_files(PREVIEWS_DIR, "*.png")
        now = time.time()
        cutoff = now - (effective_max_age_days * 24 * 60 * 60)

        output_records = []
        errors: list[dict] = []
        for path in outputs:
            try:
                stat = path.stat()
            except OSError as exc:
                errors.append(self._cleanup_error(path, exc))
                continue
            output_records.append({"path": path, "mtime": stat.st_mtime})

        planned: dict[Path, dict] = {}
        for record in output_records:
            if record["mtime"] < cutoff:
                self._plan_cleanup(
                    planned,
                    record["path"],
                    kind="output",
                    reason="age",
                )

        retained_records = [
            record for record in output_records if record["path"] not in planned
        ]
        retained_records.sort(key=lambda record: record["mtime"], reverse=True)
        for record in retained_records[effective_max_files:]:
            self._plan_cleanup(
                planned,
                record["path"],
                kind="output",
                reason="count",
            )

        metadata_by_output_name = self._metadata_by_output_name(metadata_files)
        self._expand_associated_cleanup(planned, metadata_by_output_name)

        outputs_by_stem = {path.stem: path for path in outputs}
        retained_output_stems = {
            path.stem for path in outputs if path not in planned
        }
        referenced_previews = set()
        for metadata_path in metadata_files:
            output_names = self._metadata_output_names(metadata_path)
            has_retained_output = metadata_path.stem in retained_output_stems or any(
                Path(output_name).stem in retained_output_stems
                for output_name in output_names
            )
            if has_retained_output:
                preview_name = self._metadata_preview_name(metadata_path)
                if preview_name:
                    referenced_previews.add(preview_name)

        for metadata_path in metadata_files:
            output_names = self._metadata_output_names(metadata_path)
            has_output = metadata_path.stem in outputs_by_stem or any(
                (OUTPUTS_DIR / output_name).exists()
                for output_name in output_names
            )
            if has_output:
                continue
            if self._is_older_than(metadata_path, cutoff, errors):
                self._plan_cleanup(
                    planned,
                    metadata_path,
                    kind="metadata",
                    reason="orphan",
                )

        for preview_path in preview_files:
            if preview_path.name in referenced_previews:
                continue
            if self._is_older_than(preview_path, cutoff, errors):
                self._plan_cleanup(
                    planned,
                    preview_path,
                    kind="preview",
                    reason="orphan",
                )

        removed = []
        freed_bytes = 0
        for entry in sorted(planned.values(), key=lambda item: item["filename"]):
            path = entry.pop("path")
            try:
                size = path.stat().st_size
                entry["size_bytes"] = size
                if not dry_run:
                    path.unlink()
                    freed_bytes += size
                removed.append(entry)
            except FileNotFoundError:
                continue
            except OSError as exc:
                errors.append(self._cleanup_error(path, exc))

        return {
            "dry_run": dry_run,
            "retention": {
                "max_age_days": effective_max_age_days,
                "max_files": effective_max_files,
            },
            "scanned": {
                "outputs": len(outputs),
                "metadata": len(metadata_files),
                "previews": len(preview_files),
            },
            "removed_count": len(removed),
            "removed": removed,
            "freed_bytes": 0 if dry_run else freed_bytes,
            "error_count": len(errors),
            "errors": errors,
        }

    def list_history(self, *, limit: int = 20) -> dict:
        effective_limit = min(100, self._normalize_positive_int(limit, 20))
        records = []

        for metadata_path in self._list_files(OUTPUTS_DIR, "*.json"):
            metadata = self._read_metadata(metadata_path)
            if not isinstance(metadata, dict):
                continue

            full_output = self._safe_stored_filename(
                metadata.get("full_output") or metadata.get("output"),
                ".glb",
            )
            preview_output = self._safe_stored_filename(
                metadata.get("preview_output") or full_output,
                ".glb",
            )
            preview = self._safe_stored_filename(metadata.get("preview"), ".png")
            display_output = preview_output or full_output
            if not display_output:
                continue
            if not (OUTPUTS_DIR / display_output).exists() and (
                not full_output or not (OUTPUTS_DIR / full_output).exists()
            ):
                continue

            records.append(
                {
                    "job_id": metadata.get("job_id"),
                    "status": metadata.get("status", "done"),
                    "output": metadata.get("output") or full_output,
                    "full_output": full_output,
                    "preview_output": preview_output or full_output,
                    "preview": preview,
                    "settings": metadata.get("settings") or {},
                    "diagnostics": metadata.get("diagnostics") or {},
                    "stage_timings": metadata.get("stage_timings")
                    or (metadata.get("diagnostics") or {}).get("timings")
                    or {},
                    "created_at": metadata.get("created_at"),
                    "updated_at": metadata.get("updated_at"),
                    "completed_at": metadata.get("completed_at"),
                }
            )

        records.sort(
            key=lambda item: item.get("completed_at")
            or item.get("updated_at")
            or item.get("created_at")
            or 0,
            reverse=True,
        )
        return {"count": len(records[:effective_limit]), "items": records[:effective_limit]}

    def _normalize_positive_int(self, value: int | None, default: int) -> int:
        if value is None:
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def _list_files(self, directory: Path, pattern: str) -> list[Path]:
        try:
            return sorted(path for path in directory.glob(pattern) if path.is_file())
        except OSError as exc:
            print(f"[ModelService] Could not list {directory}: {exc}")
            return []

    def _plan_cleanup(
        self,
        planned: dict[Path, dict],
        path: Path,
        *,
        kind: str,
        reason: str,
    ):
        planned.setdefault(
            path,
            {
                "path": path,
                "filename": path.name,
                "kind": kind,
                "reason": reason,
            },
        )

    def _metadata_preview_name(self, metadata_path: Path) -> str | None:
        metadata = self._read_metadata(metadata_path)
        if not isinstance(metadata, dict):
            return None
        return self._safe_stored_filename(metadata.get("preview"), ".png")

    def _metadata_output_names(self, metadata_path: Path) -> set[str]:
        metadata = self._read_metadata(metadata_path)
        if not isinstance(metadata, dict):
            return set()

        names = set()
        for key in ("output", "full_output", "preview_output"):
            name = self._safe_stored_filename(metadata.get(key), ".glb")
            if name:
                names.add(name)

        outputs = metadata.get("outputs")
        if isinstance(outputs, dict):
            for value in outputs.values():
                name = self._safe_stored_filename(value, ".glb")
                if name:
                    names.add(name)

        return names

    def _metadata_by_output_name(self, metadata_files: list[Path]) -> dict[str, Path]:
        result = {}
        for metadata_path in metadata_files:
            for output_name in self._metadata_output_names(metadata_path):
                result.setdefault(output_name, metadata_path)
            legacy_name = f"{metadata_path.stem}.glb"
            if (OUTPUTS_DIR / legacy_name).exists():
                result.setdefault(legacy_name, metadata_path)
        return result

    def _expand_associated_cleanup(
        self,
        planned: dict[Path, dict],
        metadata_by_output_name: dict[str, Path],
    ):
        changed = True
        while changed:
            changed = False
            planned_outputs = [
                entry["path"]
                for entry in planned.values()
                if entry["kind"] == "output"
            ]
            for output_path in planned_outputs:
                metadata_path = metadata_by_output_name.get(output_path.name)
                legacy_metadata_path = OUTPUTS_DIR / f"{output_path.stem}.json"
                if metadata_path is None and legacy_metadata_path.exists():
                    metadata_path = legacy_metadata_path
                if metadata_path is None or not metadata_path.exists():
                    continue

                before = len(planned)
                self._plan_cleanup(
                    planned,
                    metadata_path,
                    kind="metadata",
                    reason="associated_output",
                )
                for output_name in self._metadata_output_names(metadata_path):
                    associated_output = OUTPUTS_DIR / output_name
                    if associated_output.exists():
                        self._plan_cleanup(
                            planned,
                            associated_output,
                            kind="output",
                            reason="associated_output",
                        )
                preview_name = self._metadata_preview_name(metadata_path)
                if preview_name:
                    preview_path = PREVIEWS_DIR / preview_name
                    if preview_path.exists():
                        self._plan_cleanup(
                            planned,
                            preview_path,
                            kind="preview",
                            reason="associated_output",
                        )
                changed = changed or len(planned) != before

    def _read_metadata(self, metadata_path: Path):
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _safe_stored_filename(self, value, expected_suffix: str) -> str | None:
        if not isinstance(value, str):
            return None
        if value != Path(value).name or ".." in value:
            return None
        if not value.lower().endswith(expected_suffix):
            return None
        return value

    def _is_older_than(self, path: Path, cutoff: float, errors: list[dict]) -> bool:
        try:
            return path.stat().st_mtime < cutoff
        except OSError as exc:
            errors.append(self._cleanup_error(path, exc))
            return False

    def _cleanup_error(self, path: Path, exc: OSError) -> dict:
        return {
            "filename": path.name,
            "error": str(exc),
        }

    def _submission_key(self, image_bytes: bytes, params: dict) -> str:
        normalized_params = json.dumps(
            params,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        digest = hashlib.sha256()
        digest.update(image_bytes)
        digest.update(b"\0")
        digest.update(normalized_params)
        return digest.hexdigest()

    def _active_duplicate_job_id_locked(self, submission_key: str) -> str | None:
        job_id = self._submission_keys.get(submission_key)
        if not job_id:
            return None
        job = self._jobs.get(job_id)
        if job is None:
            self._submission_keys.pop(submission_key, None)
            return None
        if job.status in {"pending", "running"}:
            return job_id
        self._submission_keys.pop(submission_key, None)
        return None

    def _drop_submission_key_locked(self, job: Job):
        if job.submission_key and self._submission_keys.get(job.submission_key) == job.job_id:
            self._submission_keys.pop(job.submission_key, None)

    def _expire_submission_keys_locked(self):
        for submission_key, job_id in list(self._submission_keys.items()):
            job = self._jobs.get(job_id)
            if job is None or job.status not in {"pending", "running"}:
                self._submission_keys.pop(submission_key, None)

    def _queued_job_ids(self) -> list[str]:
        with self._queue.mutex:
            return [
                item[0]
                for item in list(self._queue.queue)
                if isinstance(item, tuple) and item
            ]

    def _remove_queued_job_locked(self, job_id: str) -> bool:
        with self._queue.mutex:
            for index, item in enumerate(list(self._queue.queue)):
                if not isinstance(item, tuple) or not item or item[0] != job_id:
                    continue
                del self._queue.queue[index]
                if self._queue.unfinished_tasks > 0:
                    self._queue.unfinished_tasks -= 1
                    if self._queue.unfinished_tasks == 0:
                        self._queue.all_tasks_done.notify_all()
                self._queue.not_full.notify()
                return True
        return False

    def _refresh_queue_positions_locked(self):
        for job in self._jobs.values():
            if job.status == "pending":
                job.queue_position = None

        position = 1
        for job_id in self._queued_job_ids():
            job = self._jobs.get(job_id)
            if job is None or job.status != "pending" or job.cancel_requested:
                continue
            job.queue_position = position
            job.step = f"Queued ({position})"
            job.touch()
            position += 1

    def _prune_jobs_locked(self):
        if len(self._jobs) < self._max_jobs:
            return

        finished = sorted(
            (
                (job.completed_at or job.updated_at or job.created_at),
                job_id,
            )
            for job_id, job in self._jobs.items()
            if job.status in {"done", "error", "cancelled"}
        )
        remove_count = len(self._jobs) - self._max_jobs + 1
        for _, job_id in finished[:remove_count]:
            job = self._jobs.pop(job_id, None)
            if job:
                self._drop_submission_key_locked(job)


model_service = ModelService()
