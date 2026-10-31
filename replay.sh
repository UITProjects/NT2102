#!/usr/bin/env bash
# replay.sh - replay a recorded capture into a local live server (start it first: ./serve.sh local).
#
# Usage: ./replay.sh <session-id | raw-dir> [speed]
#   session-id  a folder in src/live/sessions/ -> replays its raw/ copy
#   raw-dir     any folder of SilkETW *.json, e.g.
#               src/out/etw/experiments/20261009T145632Z/raw/train/x-20261009T145632Z
#   speed       default 120 (0 hangs, 600 makes POSTs too large and the shipper times out)
# Env: PORT (default 8000). The replay is saved as a new session named replay-<session-id> (or
# replay-<folder name> for a raw-dir).
set -euo pipefail
cd "$(dirname "$0")"
[[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]] && { sed -n '2,10p' "$0"; exit 0; }

src=$1
speed=${2:-120}
PORT=${PORT:-8000}
name=()
if [[ -d "src/live/sessions/$src/raw" ]]; then
  name=(--session "replay-$src")   # a session's copy is always called raw/: name the replay after the session
  src="src/live/sessions/$src/raw"
elif [[ ! -d "$src" ]]; then
  echo "[replay] no session src/live/sessions/$src and no folder $src" >&2
  exit 1
fi
if ! curl -sf "http://127.0.0.1:$PORT/api/status" >/dev/null; then
  echo "[replay] no live server on 127.0.0.1:$PORT. Start one first: ./serve.sh local" >&2
  exit 1
fi
exec uv run python src/live/etwship.py --server "http://127.0.0.1:$PORT" --speed "$speed" --replay "$src" "${name[@]}"
