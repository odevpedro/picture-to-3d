import io
import json
from pathlib import Path, PurePath, PureWindowsPath

from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from api.services.model_service import (
    JobQueueFull,
    model_service,
    OUTPUTS_DIR,
    PREVIEWS_DIR,
)

router = APIRouter(prefix="/api", tags=["generate"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MIN_IMAGE_SIDE = 64
SUPPORTED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
MIN_RESOLUTION = 64
MAX_RESOLUTION = 384
MIN_MC_THRESHOLD = 1.0
MAX_MC_THRESHOLD = 80.0
MIN_FOREGROUND_RATIO = 0.55
MAX_FOREGROUND_RATIO = 0.95
MIN_EXTRUDE_DEPTH = 0.01
MAX_EXTRUDE_DEPTH = 0.30
MIN_ALPHA_THRESHOLD = 1
MAX_ALPHA_THRESHOLD = 254
MIN_MASK_BIAS = -8
MAX_MASK_BIAS = 8
MAX_MASK_EDITS = 500
GENERATION_PRESETS = {
    "fast": {
        "resolution": 128,
        "mc_threshold": 28.0,
        "smoothing_iterations": 0,
    },
    "balanced": {
        "resolution": 256,
        "mc_threshold": 25.0,
        "smoothing_iterations": 2,
    },
    "high": {
        "resolution": 320,
        "mc_threshold": 22.0,
        "smoothing_iterations": 3,
    },
}


@router.post("/generate")
async def generate(
    image: UploadFile = File(...),
    preset: str = Form("balanced"),
    advanced: bool = Form(False),
    mode: str = Form("auto"),
    input_source: str = Form("sanitized"),
    object_type: str = Form("auto"),
    foreground_ratio: float = Form(0.84),
    extrude_depth: float = Form(0.08),
    alpha_threshold: int = Form(8),
    mask_bias: int = Form(0),
    mask_edits: str = Form("[]"),
    resolution: int   = Form(256),
    mc_threshold: float = Form(25.0),
):
    if not image.content_type or not image.content_type.startswith("image/"):
        _bad_request("invalid_content_type", "File must be an image")
    if image.content_type not in SUPPORTED_CONTENT_TYPES:
        supported = ", ".join(sorted(SUPPORTED_CONTENT_TYPES))
        _bad_request(
            "unsupported_image_type",
            f"Unsupported image type. Supported types: {supported}",
            {"content_type": image.content_type},
        )

    image_bytes = await image.read()
    _validate_upload_size(image_bytes)
    image_info = _validate_image_bytes(image_bytes)

    params = _resolve_generation_params(
        preset,
        advanced,
        mode,
        input_source,
        object_type,
        foreground_ratio,
        extrude_depth,
        alpha_threshold,
        mask_bias,
        mask_edits,
        resolution,
        mc_threshold,
    )
    params["input"] = image_info
    try:
        job_id = model_service.submit(image_bytes, params)
    except JobQueueFull as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "generation_queue_full",
                "message": "Generation queue is full. Try again after a current job finishes.",
                "meta": {"max_queue_size": exc.max_queue_size},
            },
        ) from exc
    return {"job_id": job_id, "params": params}


@router.post("/preprocess")
async def preprocess(
    image: UploadFile = File(...),
    input_source: str = Form("sanitized"),
    foreground_ratio: float = Form(0.84),
    alpha_threshold: int = Form(8),
    mask_bias: int = Form(0),
    mask_edits: str = Form("[]"),
):
    if not image.content_type or not image.content_type.startswith("image/"):
        _bad_request("invalid_content_type", "File must be an image")
    if image.content_type not in SUPPORTED_CONTENT_TYPES:
        supported = ", ".join(sorted(SUPPORTED_CONTENT_TYPES))
        _bad_request(
            "unsupported_image_type",
            f"Unsupported image type. Supported types: {supported}",
            {"content_type": image.content_type},
        )

    image_bytes = await image.read()
    _validate_upload_size(image_bytes)
    image_info = _validate_image_bytes(image_bytes)
    params = _resolve_preprocess_params(
        input_source,
        foreground_ratio,
        alpha_threshold,
        mask_bias,
        mask_edits,
    )
    params["input"] = image_info
    preview_result = model_service.prepare_preview(image_bytes, params)
    return {"params": params, **preview_result}


@router.get("/status/{job_id}")
async def status(job_id: str):
    job = model_service.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return _job_response(job)


@router.post("/cancel/{job_id}")
async def cancel(job_id: str):
    job = model_service.cancel_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return _job_response(job)


def _job_response(job):
    return {
        "job_id":   job.job_id,
        "status":   job.status,
        "progress": job.progress,
        "step":     job.step,
        "output":   job.output,
        "full_output": getattr(job, "full_output", None) or job.output,
        "preview_output": getattr(job, "preview_output", None) or job.output,
        "preview":  job.preview,
        "error":    job.error,
        "diagnostics": job.diagnostics,
        "stage_timings": getattr(job, "stage_timings", None),
        "created_at": getattr(job, "created_at", None),
        "updated_at": getattr(job, "updated_at", None),
        "completed_at": getattr(job, "completed_at", None),
        "queued_at": getattr(job, "queued_at", None),
        "started_at": getattr(job, "started_at", None),
        "queue_position": getattr(job, "queue_position", None),
        "cancel_requested": getattr(job, "cancel_requested", False),
        "cancelled_at": getattr(job, "cancelled_at", None),
        "timeout_seconds": getattr(job, "timeout_seconds", None),
        "queue": {
            "position": getattr(job, "queue_position", None),
            "worker_count": getattr(model_service, "worker_count", None),
            "max_queue_size": getattr(model_service, "max_queue_size", None),
        },
        "settings": getattr(job, "settings", None),
    }


@router.get("/download/{filename}")
async def download(filename: str):
    path = _resolve_safe_file(OUTPUTS_DIR, filename, expected_suffix=".glb")
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        str(path),
        media_type="model/gltf-binary",
        filename=filename,
    )


@router.get("/preview/{filename}")
async def preview(filename: str):
    path = _resolve_safe_file(PREVIEWS_DIR, filename, expected_suffix=".png")
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        str(path),
        media_type="image/png",
        filename=filename,
    )


@router.get("/device")
async def device_info():
    return {"device": model_service.device_name}


@router.get("/preflight")
async def preflight():
    return model_service.preflight()


@router.get("/history")
async def history(limit: int = 20):
    return model_service.list_history(limit=limit)


@router.post("/cleanup")
async def cleanup_outputs(
    dry_run: bool = False,
    max_age_days: int | None = None,
    max_files: int | None = None,
):
    return model_service.cleanup_outputs(
        dry_run=dry_run,
        max_age_days=max_age_days,
        max_files=max_files,
    )


def _bad_request(code: str, message: str, meta: dict | None = None):
    raise HTTPException(
        status_code=400,
        detail={
            "code": code,
            "message": message,
            "meta": meta or {},
        },
    )


def _resolve_safe_file(base_dir: Path, filename: str, *, expected_suffix: str) -> Path:
    if not filename or filename in {".", ".."}:
        raise HTTPException(400, "Invalid filename")

    posix_name = PurePath(filename).name
    windows_name = PureWindowsPath(filename).name
    if filename != posix_name or filename != windows_name or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    if not filename.lower().endswith(expected_suffix):
        raise HTTPException(400, "Invalid filename")

    return base_dir / filename


def _validate_upload_size(image_bytes: bytes):
    size = len(image_bytes)
    if size == 0:
        _bad_request("empty_upload", "Uploaded image is empty")
    if size > MAX_UPLOAD_BYTES:
        _bad_request(
            "upload_too_large",
            f"Image is too large. Maximum size is {MAX_UPLOAD_BYTES // 1024 // 1024} MB",
            {"size_bytes": size, "max_bytes": MAX_UPLOAD_BYTES},
        )


def _validate_image_bytes(image_bytes: bytes) -> dict:
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
            fmt = img.format
    except UnidentifiedImageError:
        _bad_request("invalid_image", "Uploaded file could not be decoded as an image")
    except Exception as exc:
        _bad_request("invalid_image", "Uploaded image failed validation", {"error": str(exc)})

    pixels = width * height
    if width < MIN_IMAGE_SIDE or height < MIN_IMAGE_SIDE:
        _bad_request(
            "image_too_small",
            f"Image is too small. Minimum side is {MIN_IMAGE_SIDE}px",
            {"width": width, "height": height, "min_side": MIN_IMAGE_SIDE},
        )
    if pixels > MAX_IMAGE_PIXELS:
        _bad_request(
            "image_too_large",
            f"Image has too many pixels. Maximum is {MAX_IMAGE_PIXELS}",
            {"width": width, "height": height, "pixels": pixels, "max_pixels": MAX_IMAGE_PIXELS},
        )

    return {
        "width": width,
        "height": height,
        "format": fmt,
        "size_bytes": len(image_bytes),
    }


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        _bad_request(
            "invalid_numeric_value",
            "Numeric parameter is invalid",
            {"value": str(value)},
        )
    return max(minimum, min(maximum, parsed))


def _resolve_generation_params(
    preset: str,
    advanced: bool,
    mode: str,
    input_source: str,
    object_type: str,
    foreground_ratio: float,
    extrude_depth: float,
    alpha_threshold: int,
    mask_bias: int,
    mask_edits: str,
    resolution: int,
    mc_threshold: float,
) -> dict:
    preset_key = preset.strip().lower()
    if preset_key not in GENERATION_PRESETS:
        _bad_request(
            "invalid_preset",
            "Invalid generation preset",
            {"preset": preset, "supported": sorted(GENERATION_PRESETS)},
        )

    resolved = dict(GENERATION_PRESETS[preset_key])
    if advanced:
        resolved["resolution"] = _clamp_int(resolution, MIN_RESOLUTION, MAX_RESOLUTION)
        resolved["mc_threshold"] = _clamp_float(
            mc_threshold,
            MIN_MC_THRESHOLD,
            MAX_MC_THRESHOLD,
        )

    resolved["preset"] = preset_key
    resolved["advanced"] = advanced
    resolved["mode"] = _resolve_generation_mode(mode)
    resolved["input_source"] = _resolve_input_source(input_source)
    resolved["object_type"] = _resolve_object_type(object_type)
    resolved["foreground_ratio"] = _clamp_float(
        foreground_ratio,
        MIN_FOREGROUND_RATIO,
        MAX_FOREGROUND_RATIO,
    )
    resolved["extrude_depth"] = _clamp_float(
        extrude_depth,
        MIN_EXTRUDE_DEPTH,
        MAX_EXTRUDE_DEPTH,
    )
    resolved["alpha_threshold"] = _clamp_int(
        alpha_threshold,
        MIN_ALPHA_THRESHOLD,
        MAX_ALPHA_THRESHOLD,
    )
    resolved["mask_bias"] = _clamp_int(mask_bias, MIN_MASK_BIAS, MAX_MASK_BIAS)
    resolved["mask_edits"] = _resolve_mask_edits(mask_edits)
    return resolved


def _resolve_preprocess_params(
    input_source: str,
    foreground_ratio: float,
    alpha_threshold: int,
    mask_bias: int,
    mask_edits: str,
) -> dict:
    return {
        "input_source": _resolve_input_source(input_source),
        "foreground_ratio": _clamp_float(
            foreground_ratio,
            MIN_FOREGROUND_RATIO,
            MAX_FOREGROUND_RATIO,
        ),
        "alpha_threshold": _clamp_int(
            alpha_threshold,
            MIN_ALPHA_THRESHOLD,
            MAX_ALPHA_THRESHOLD,
        ),
        "mask_bias": _clamp_int(mask_bias, MIN_MASK_BIAS, MAX_MASK_BIAS),
        "mask_edits": _resolve_mask_edits(mask_edits),
    }


def _resolve_generation_mode(mode: str) -> str:
    mode_key = mode.strip().lower()
    supported = {"auto", "ai", "silhouette"}
    if mode_key not in supported:
        _bad_request(
            "invalid_generation_mode",
            "Invalid generation mode",
            {"mode": mode, "supported": sorted(supported)},
        )
    return mode_key


def _resolve_input_source(input_source: str) -> str:
    source_key = input_source.strip().lower()
    supported = {"sanitized", "original"}
    if source_key not in supported:
        _bad_request(
            "invalid_input_source",
            "Invalid model input source",
            {"input_source": input_source, "supported": sorted(supported)},
        )
    return source_key


def _resolve_object_type(object_type: str) -> str:
    object_type_key = object_type.strip().lower()
    supported = {"auto", "thin", "icon", "rounded"}
    if object_type_key not in supported:
        _bad_request(
            "invalid_object_type",
            "Invalid object type preset",
            {"object_type": object_type, "supported": sorted(supported)},
        )
    return object_type_key


def _resolve_mask_edits(mask_edits: str) -> list[dict]:
    if not mask_edits:
        return []
    try:
        parsed = json.loads(mask_edits)
    except json.JSONDecodeError:
        _bad_request("invalid_mask_edits", "Manual mask edits must be valid JSON")

    if not isinstance(parsed, list):
        _bad_request("invalid_mask_edits", "Manual mask edits must be a list")
    if len(parsed) > MAX_MASK_EDITS:
        _bad_request(
            "too_many_mask_edits",
            f"Manual mask edits are limited to {MAX_MASK_EDITS} strokes",
            {"count": len(parsed), "max": MAX_MASK_EDITS},
        )

    edits = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("mode", "erase")).strip().lower()
        if mode not in {"erase", "restore"}:
            continue
        edits.append(
            {
                "mode": mode,
                "x": _clamp_float(item.get("x", 0.5), 0.0, 1.0),
                "y": _clamp_float(item.get("y", 0.5), 0.0, 1.0),
                "radius": _clamp_float(item.get("radius", 0.04), 0.002, 0.3),
            }
        )
    return edits
