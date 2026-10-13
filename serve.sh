#!/usr/bin/env bash
# serve.sh - start the live server (src/live/live_server.py) with the current model.
#
# Usage: ./serve.sh [live|local] [extra live_server.py flags...]
#   live   (default) bind 10.21.2.1, the host-only IP the VM shipper posts to
#   local  bind 127.0.0.1, for replays (./replay.sh)
# Examples:
#   ./serve.sh                                  # demo with the VM
#   ./serve.sh local --llm-provider mock --sg-moderate-from 10.0001   # UI test: 11:25 replay window red + mock verdict
#   ./serve.sh local --stride-min 2 --origin-ms 1791557793343   # training-capture replay check
# Env overrides: RUN_DIR, SCALER, EXPERIMENT, PORT (default 8000). UI: http://<bind>:<port>/
set -euo pipefail
cd "$(dirname "$0")"
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { sed -n '2,12p' "$0"; exit 0; }

RUN_DIR=${RUN_DIR:-src/artifacts/runs/exp01}
SCALER=${SCALER:-src/artifacts/features/self-dataset/feature_scaler.json}
EXPERIMENT=${EXPERIMENT:-src/out/etw/experiments/20261009T145632Z/experiment.json}
PORT=${PORT:-8000}

mode=live
if [[ "${1:-}" == "live" || "${1:-}" == "local" ]]; then
  mode=$1
  shift
fi
bind=10.21.2.1
[[ $mode == local ]] && bind=127.0.0.1

# extra flags go last, so they override the defaults above (argparse keeps the last value)
exec uv run python src/live/live_server.py --bind "$bind" --port "$PORT" --run-dir "$RUN_DIR" \
  --scaler "$SCALER" --experiment "$EXPERIMENT" "$@"
