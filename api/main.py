from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from contextlib import asynccontextmanager
import json
import os

from api.routers import generate
from api.services.model_service import model_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[Server] Starting up...")
    cleanup = model_service.cleanup_outputs()
    if cleanup["removed_count"] or cleanup["error_count"]:
        print(
            "[Server] Output cleanup: "
            f"{cleanup['removed_count']} removed, {cleanup['error_count']} errors"
        )
    yield
    print("[Server] Shutting down...")
    model_service.unload()


app = FastAPI(title="Image to 3D", lifespan=lifespan)

app.include_router(generate.router)

# Serve frontend
frontend_dir = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def root():
    return FileResponse(str(frontend_dir / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok", "device": model_service.device_name}


def serve():
    import uvicorn

    host = os.environ.get("IMAGE3D_HOST", "127.0.0.1")
    port = int(os.environ.get("IMAGE3D_PORT", "8080"))
    uvicorn.run("api.main:app", host=host, port=port, reload=True)


def warmup():
    result = model_service.warmup()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Image to 3D server utilities")
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="download/validate TripoSR cache and load the model once",
    )
    args = parser.parse_args()
    if args.warmup:
        warmup()
    else:
        serve()
