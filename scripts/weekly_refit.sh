#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
LOG_DIR="$(pwd)/logs"
LOG_FILE="$LOG_DIR/weekly-$(date +%Y-%m-%d).log"
mkdir -p "$LOG_DIR"

{
  echo "============================================================"
  echo "[weekly] start $(date -u +%FT%TZ) | local $(date)"
  echo "============================================================"
  ./venv/bin/python -c "from src.fetch import fetch_all; fetch_all()"
  ./venv/bin/python -c "from src.features import build_all; build_all()"
  ./venv/bin/python -c "from src.model import train_all; train_all()"
  ./venv/bin/python -c "from src.inference import compute_history_all; compute_history_all()"
  echo "[weekly] done $(date -u +%FT%TZ)"
} >> "$LOG_FILE" 2>&1
