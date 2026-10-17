#!/usr/bin/env bash
# selftest.sh - run the pipeline/live selftests listed in this file. Proves "it runs", never "it detects attacks".
#
# Usage: ./selftest.sh
# Runs all steps even if one fails, prints PASS/FAIL per step, exits 1 if any failed.
set -euo pipefail
cd "$(dirname "$0")"
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { sed -n '2,5p' "$0"; exit 0; }

steps=(
  "src/pipeline/train_gnn_models.py --selftest"
  "src/pipeline/score_window.py --selftest"
  "src/pipeline/alg1.py --selftest"
  "src/pipeline/alg2.py --selftest"
  "src/live/engine.py --selftest"
  "src/live/etwship.py --selftest"
  "src/pipeline/etw2nodlink.py --selftest"
  "src/pipeline/etwwindow.py --selftest"
  "src/pipeline/lokiclient.py --selftest"
  "src/pipeline/loki_pull.py --selftest"
  "src/pipeline/build_experiment.py --selftest"
  "src/live/loki_push.py --selftest"
  "src/live/loki_source.py --selftest"
  "-m lab selftest"
)
log_dir=$(mktemp -d)
failed=0
for i in "${!steps[@]}"; do
  step=${steps[$i]}
  log="$log_dir/$i.log"
  # word splitting of $step is intended: "<script> <flag>"
  # shellcheck disable=SC2086
  if uv run python $step >"$log" 2>&1; then
    echo "PASS  $step"
  else
    echo "FAIL  $step   (last lines below, full log: $log)"
    tail -n 15 "$log" | sed 's/^/      /'
    failed=1
  fi
done
[[ $failed -eq 0 ]] && rm -rf "$log_dir"
exit $failed
