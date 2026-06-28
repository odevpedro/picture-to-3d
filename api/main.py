from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from contextlib import asynccontextmanager

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

    uvicorn.run("api.main:app", host="0.0.0.0", port=8080, reload=True)
