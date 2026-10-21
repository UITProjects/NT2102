#!/usr/bin/env bash
# rejudge.sh - re-run Alg. 1 + SG-Det and the LLM step on a saved live session (no VM, no server).
#
# Usage: ./rejudge.sh <session-id> [llm-provider] [extra alg1.py flags...]
#   no provider -> prints the LLM input documents of every red window (alg2.py --serialize-only, no call)
#   provider    -> anthropic | openai | ollama | mock: writes windows/<wid>/llm/ (needs: uv sync --group llm)
# Examples:
#   ./rejudge.sh replay-live-20261011T112141Z
#   ./rejudge.sh replay-live-20261011T112141Z anthropic
#   ./rejudge.sh replay-live-20261011T112141Z "" --sg-min-score 20    # other cut-off, no LLM
# Env: RUN_DIR (default: the current model, `lab current --field run_dir`).
set -euo pipefail
cd "$(dirname "$0")"
[[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]] && { sed -n '2,11p' "$0"; exit 0; }

session=$1
provider=${2:-}
shift $(( $# >= 2 ? 2 : 1 ))
RUN_DIR=${RUN_DIR:-$(uv run lab current --field run_dir)}
sdir="src/live/sessions/$session"
[[ -d "$sdir/windows" ]] || { echo "[rejudge] no session at $sdir" >&2; exit 1; }

# alg1 writes subgraphs_alg1.json per window; alg2 reads that file so both use the same cut-off
uv run python src/pipeline/alg1.py --run-dir "$RUN_DIR" --session-dir "$sdir" "$@"
if [[ -z "$provider" ]]; then
  uv run python src/pipeline/alg2.py --session-dir "$sdir" --subgraphs-file subgraphs_alg1.json --serialize-only
else
  uv run --group llm python src/pipeline/alg2.py --session-dir "$sdir" --subgraphs-file subgraphs_alg1.json \
    --llm-provider "$provider"
fi
