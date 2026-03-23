import os
from pathlib import Path
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse

from api.services.model_service import model_service, OUTPUTS_DIR

router = APIRouter(prefix="/api", tags=["generate"])


@router.post("/generate")
async def generate(
    image: UploadFile = File(...),
    resolution: int   = Form(256),
    mc_threshold: float = Form(25.0),
):
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    image_bytes = await image.read()
    params = {
        "resolution":    resolution,
        "mc_threshold":  mc_threshold,
    }
    job_id = model_service.submit(image_bytes, params)
    return {"job_id": job_id}


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
        "error":    job.error,
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


@router.get("/device")
async def device_info():
    return {"device": model_service.device_name}
