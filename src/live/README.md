# Live demo: VM shipper → host scoring → browser

```
VM  SilkService → raw\test\live-<UTC>\{kfile,knet,kproc}.json
    08-live-shipper.ps1 → etwship.py (tail + POST every 1 s) ──HTTP──► 10.21.2.1:8000
host live_server.py → engine.py: reorder by event time → etw2nodlink.Graph (same code as training,
     taken from scripts/04-collector.ps1) → 5-min windows → train scaler + train relations →
     OCRGCN per type → score > τ → Alg. 1 subgraphs + SG-Det (alert = ≥ 1 subgraph ≥ Moderate)
     → sessions/<id>/windows/<wid>/ + UI (SSE)
```

| file | role |
|---|---|
| `etwship.py` | stdlib shipper. Live tail on the VM, `--replay` on the host, `--selftest` |
| `../scripts/08-live-shipper.ps1` | VM entry point: starts a capture, runs the embedded copy of etwship.py |
| `sync_ps1.py` | copies `etwship.py` into the 08 script (run after editing etwship.py) |
| `engine.py` | ingest, reorder buffer, windows, scoring thread, calls Alg. 1 (`--selftest`) |
| `live_server.py` | FastAPI: `/ingest`, `/api/*`, SSE `/api/stream`, UI at `/` |
| `static/index.html` | UI |
| `compare_replay.py` | checks that the live path reproduces the offline pipeline on the training capture |
| `../pipeline/score_window.py` | the scorer shared with offline test scoring (`--selftest`) |
| `../pipeline/alg1.py` | Alg. 1 subgraphs + subgraph-score filter; re-judges saved sessions (`--selftest`) |

Run everything from the repo root. First, once: `uv add fastapi "uvicorn[standard]"`.

## 1. It runs (selftests)

```
uv run python src/pipeline/train_gnn_models.py --selftest
uv run python src/pipeline/score_window.py --selftest
uv run python src/pipeline/alg1.py --selftest
uv run python src/live/engine.py --selftest
uv run python src/live/etwship.py --selftest
uv run python src/live/sync_ps1.py --check
```

## 2. Prerequisite: a valid model

Use `src/artifacts/runs/exp01`, the current valid run (docs/PROGRESS.md). A retrained run on wider benign
data (PROGRESS "Next 3 steps" #2) goes next to it as `src/artifacts/runs/exp02`.
The server refuses a run whose `runs_summary.csv` shows `emb_std ≈ 0`.

## 3. Offline scorer reproduces the training scores

```
uv run python src/pipeline/score_window.py --run-dir src/artifacts/runs/exp01 \
  --edges src/out/etw/experiments/20261009T145632Z/SimulatedW10_train_edges.csv \
  --scaler src/artifacts/features/self-dataset/feature_scaler.json
```
Expect a flag rate per type ≈ `train_flag_rate` in runs_summary.csv. The scores should equal train_scores.csv.

## 4. Works correctly: replay the training capture (no VM)

```
# terminal 1: grid aligned to training window w0000 (start_ms from windows.csv), stride 2 like training
uv run python src/live/live_server.py --bind 127.0.0.1 --run-dir src/artifacts/runs/exp01 \
  --scaler src/artifacts/features/self-dataset/feature_scaler.json \
  --experiment src/out/etw/experiments/20261009T145632Z/experiment.json \
  --stride-min 2 --origin-ms 1791557793343
# terminal 2 (20 min of capture at 60x ≈ 30 s)
uv run python src/live/etwship.py --server http://127.0.0.1:8000 \
  --replay src/out/etw/experiments/20261009T145632Z/raw/train/x-20261009T145632Z
# then
uv run python src/live/compare_replay.py --session src/live/sessions/replay-x-20261009T145632Z \
  --experiment src/out/etw/experiments/20261009T145632Z \
  --features src/artifacts/features/self-dataset/features_model.csv \
  --train-scores src/artifacts/runs/exp01/train_scores.csv
```
Open http://127.0.0.1:8000 during the replay to watch it. Expect `ALL MATCH`. The dos-map is inferred from
the raw paths (`\device\harddiskvolume4` → `c:` on this VM).

## 5. Live

Host:
```
uv run python src/live/live_server.py --bind 10.21.2.1 --run-dir src/artifacts/runs/exp01 \
  --scaler src/artifacts/features/self-dataset/feature_scaler.json \
  --experiment src/out/etw/experiments/20261009T145632Z/experiment.json
# only if ufw is active (find the interface with: ip -br addr | grep 10.21.2.1)
sudo ufw allow in on vboxnet0 to 10.21.2.1 port 8000 proto tcp
```
VM, elevated PowerShell (copy `08-live-shipper.ps1` in first if the VM is isolated and C:\scripts is not synced):
```
C:\scripts\08-live-shipper.ps1             # shell 1: capture + ship
C:\scripts\06-benign-workload.ps1          # shell 2: benign activity, as in training
```
In the UI at http://10.21.2.1:8000, a window closes about 20 s (`--lateness-s`) after each 5-min boundary.
Benign windows should be mostly green, with a flag rate near the contamination rate (0.1 %). Run one Atomic
scenario just after a boundary and watch that window.

### Alert rule (Alg. 1 + SG-Det, `src/pipeline/alg1.py`, DEVIATIONS D8)
A window is red only if ≥ 1 subgraph reaches the cut-off. The subgraph score is Σ prob over its anomalous
nodes, where prob = (score − train min) / (train max − train min), clamped to [0, 1]. Default: ≥ Moderate
(score > 10). Subgraphs below the cut-off are still listed (dashed buttons in the UI).
Flags: `--n-seed 15 --min-nodes 3 --max-edges 5000 --sg-min-level Moderate [--sg-min-score X]`.
To try another cut-off on a saved session without the VM (writes `subgraphs_alg1.{json,csv}` per window):
```
uv run python src/pipeline/alg1.py --run-dir src/artifacts/runs/exp01 \
  --session-dir src/live/sessions/<id> [--sg-min-score X]
```
Replay speed: use `--speed 120` (0 hangs; 600 makes POSTs too large and the shipper times out).

A red window is an observation, not a detection result, until the window has labels (docs/DEVIATIONS.md D6, D8).

## Outputs per session (`src/live/sessions/<id>/`)
`session.json` (model, scaler, converter sha256, dos_map, parameters) · `raw/<stream>.json` (verbatim, can be
replayed or converted into a labelled test split) · `windows.csv` · `windows/<wid>/{edges.csv, node_attrs.csv,
features_model.csv, scores.csv, subgraphs.json}`; `windows.csv` has `subgraphs_kept, max_sg_score,
max_level, alert`.
