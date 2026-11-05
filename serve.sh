#!/usr/bin/env bash
# serve.sh - start the live server (src/live/live_server.py) with the current model, bound to
# 127.0.0.1, for replays (./replay.sh) and local UI testing. Production (ocr-live on the trainer) runs
# live_server.py directly with --loki-url, not through this script.
# Examples:
#   ./serve.sh                                                        # replay/dev server
#   ./serve.sh --llm-provider mock --sg-moderate-from 10.0001          # UI test: 11:25 replay window red + mock verdict
#   ./serve.sh --stride-min 2 --origin-ms 1791557793343                # training-capture replay check
# Model: the current-model pointer (`lab promote <run>`; src/artifacts/current*.json).
# Env overrides: RUN_DIR, SCALER, EXPERIMENT (all three to bypass the pointer), PORT (default 8000).
# UI: http://127.0.0.1:<port>/
set -euo pipefail
cd "$(dirname "$0")"
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { sed -n '2,9p' "$0"; exit 0; }

PORT=${PORT:-8000}
bind=127.0.0.1

model=()
[[ -n "${RUN_DIR:-}" ]] && model+=(--run-dir "$RUN_DIR")
[[ -n "${SCALER:-}" ]] && model+=(--scaler "$SCALER")
[[ -n "${EXPERIMENT:-}" ]] && model+=(--experiment "$EXPERIMENT")
# extra flags go last, so they override the defaults above (argparse keeps the last value)
exec uv run python src/live/live_server.py --bind "$bind" --port "$PORT" "${model[@]}" "$@"
