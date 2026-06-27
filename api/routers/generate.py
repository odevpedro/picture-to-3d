import io

from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from api.services.model_service import model_service, OUTPUTS_DIR, PREVIEWS_DIR

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

    params = _resolve_generation_params(preset, advanced, resolution, mc_threshold)
    params["input"] = image_info
    job_id = model_service.submit(image_bytes, params)
    return {"job_id": job_id, "params": params}


@router.get("/status/{job_id}")
async def status(job_id: str):
    job = model_service.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return {
        "job_id":   job.job_id,
        "status":   job.status,
        "progress": job.progress,
        "step":     job.step,
        "output":   job.output,
        "preview":  job.preview,
        "error":    job.error,
        "diagnostics": job.diagnostics,
    }


@router.get("/download/{filename}")
async def download(filename: str):
    # Security: no path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    path = OUTPUTS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        str(path),
        media_type="model/gltf-binary",
        filename=filename,
    )


@router.get("/preview/{filename}")
async def preview(filename: str):
    # Security: no path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    path = PREVIEWS_DIR / filename
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


def _bad_request(code: str, message: str, meta: dict | None = None):
    raise HTTPException(
        status_code=400,
        detail={
            "code": code,
            "message": message,
            "meta": meta or {},
        },
    )


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
    return max(minimum, min(maximum, float(value)))


def _resolve_generation_params(
    preset: str,
    advanced: bool,
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
    return resolved
