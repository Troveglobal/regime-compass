#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
LOG_DIR="$(pwd)/logs"
LOG_FILE="$LOG_DIR/daily-$(date +%Y-%m-%d).log"
mkdir -p "$LOG_DIR"

{
  echo "============================================================"
  echo "[daily] start $(date -u +%FT%TZ) | local $(date)"
  echo "============================================================"
  ./venv/bin/python -c "from src.inference import update_today_all; update_today_all()"
  echo "[daily] running alert dispatch..."
  ./venv/bin/python -c "from src.alerts import detect_and_send; import json; print(json.dumps(detect_and_send(), indent=2))"
  echo "[daily] done $(date -u +%FT%TZ)"
} >> "$LOG_FILE" 2>&1
