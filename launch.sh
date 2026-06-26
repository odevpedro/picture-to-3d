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

# Create venv + install deps if needed
if [ ! -f ".deps_installed" ]; then
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
echo "[INFO] Starting server..."
echo "[INFO] Open http://localhost:8080 in your browser"
echo "[INFO] Press CTRL+C to stop"
echo ""
uv run uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
