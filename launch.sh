#!/usr/bin/env bash
set -euo pipefail

TITLE="Image to 3D — GPU Launcher"
echo "================================"
echo " $TITLE"
echo "================================"
echo ""

# Check that uv is available
if ! command -v uv &>/dev/null; then
    echo "[ERROR] uv not found. Install it first: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

needs_setup=0
if [ ! -f ".deps_installed" ] || [ ! -x ".venv/bin/python" ]; then
    needs_setup=1
elif ! .venv/bin/python -c "import torch; raise SystemExit(0 if getattr(torch.version, 'hip', None) else 1)" >/dev/null 2>&1; then
    echo "[SETUP] Existing environment is missing ROCm torch; reinstalling GPU dependencies..."
    needs_setup=1
fi

# Create venv + install deps if needed
if [ "$needs_setup" -eq 1 ]; then
    echo "[SETUP] Creating virtual environment and installing dependencies..."
    uv venv --python 3.12
    # Install torch + torchvision from the ROCm index first
    uv pip install "torch==2.12.1+rocm7.1" --index-url https://download.pytorch.org/whl/rocm7.1
    uv pip install "torchvision==0.27.1+rocm7.1" --index-url https://download.pytorch.org/whl/rocm7.1
    # Install remaining deps
    uv pip install -r requirements.txt
    touch .deps_installed
    echo "[SETUP] Dependencies installed."
    echo ""
fi

# Start server
HOST="${IMAGE3D_HOST:-127.0.0.1}"
PORT="${IMAGE3D_PORT:-8080}"
if [ "${IMAGE3D_LAN:-0}" = "1" ]; then
    HOST="0.0.0.0"
fi
echo "[INFO] Starting server..."
echo "[INFO] Binding to ${HOST}:${PORT}"
echo "[INFO] Open http://localhost:${PORT} in your browser"
if [ "$HOST" = "0.0.0.0" ]; then
    echo "[WARN] LAN exposure is enabled. Upload and generation routes are unauthenticated."
fi
echo "[INFO] Press CTRL+C to stop"
echo ""
uv run --no-sync uvicorn api.main:app --host "$HOST" --port "$PORT" --reload
