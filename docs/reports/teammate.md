# NT2102 team handoff: OCR-APT live demo (status 2026-10-11)

Practical guide for a teammate joining the project: what exists, how to run it, what is still open.
Source of truth for status is `docs/PROGRESS.md`. Deviations from the paper are in `docs/DEVIATIONS.md`.

## 1. What the project is

We build a smaller version of **OCR-APT** (Aly, Mansour, Youssef, CCS 2025). It trains one-class graph
neural networks on benign host audit logs and flags process, file and network nodes that don't fit.
We keep the paper's core detector (OCRGCN: one RGCN + hypersphere model per node type, on behaviour
features) and run it on **our own Windows 10 ETW logs**. A live demo scores every 5-minute window.
Scope is about 40 % of the paper. Alg. 1 is simplified, and the LLM investigator (Alg. 2) is not built.

```
VM (Windows 10)                                   host (Linux, uv)
SilkETW: Kernel-Process / File / Network
  └─ 07: capture → etw2nodlink → windows ──copy──► features (encode_to_features.py)
                                                    └─ trainer (train_gnn_models.py) → runs/exp01
  └─ 08: live capture → etwship.py ──HTTP──────────► live_server.py → engine.py
                                    10.21.2.1:8000     reorder → convert → 5-min window → score → UI
```

## 2. Repo map

| where | what |
|---|---|
| `src/Vagrantfile`, `src/scripts/01..08-*.ps1` | Windows 10 lab VM, provisioned in order: base, tools, workload servers, collector + converter, 20-min capture, benign workload generator, train-dataset orchestrator, live shipper |
| `src/graphdb/compose.yaml` | GraphDB 10.8.0 (only needed for later Alg. 1 work, not for the demo) |
| `src/out/etw/experiments/<id>/` | one capture: raw ETW, converted edges, windowed edges, `windows.csv`, `experiment.json` |
| `src/pipeline/encode_to_features.py` | 15 behaviour features per node (scaler fit on train only) |
| `src/pipeline/train_gnn_models.py` | our OCRGCN trainer (one model per node type) |
| `src/pipeline/score_window.py` | scores any edge CSV with the frozen model, scaler and relation list |
| `src/artifacts/features/self-dataset/` | our features (`features_model.csv`) + `feature_scaler.json` (fit on train, reused live) |
| `src/artifacts/runs/exp01/` | the current valid model |
| `src/live/` | live demo: `etwship.py` (shipper), `engine.py`, `live_server.py` + `static/index.html`, `compare_replay.py`; run guide in `src/live/README.md` |
| `src/atomic-attack-pack/` | 10 attack scenarios + labelling scheme (not yet run) |
| `src/notebooks/01..05-*/` | EDA notebooks, numbered in reading order |
| `docs/` | `PROGRESS.md`, `DEVIATIONS.md`, these reports, `explainers/` |

## 3. How to run

Python dependencies are managed with **uv**. Run commands from the repo root.

**Selftests (prove "it runs", not "it detects"):**
```
uv run python src/pipeline/train_gnn_models.py --selftest
uv run python src/pipeline/score_window.py --selftest
uv run python src/live/engine.py --selftest
uv run python src/live/etwship.py --selftest
uv run python src/live/sync_ps1.py --check
```

**VM:** `cd src && vagrant up` builds `w10-etw` (8 GB RAM, 4 CPUs, host-only network 10.21.2.0/24,
VM at 10.21.2.10). Keep **3D acceleration off**: with it on, the NVIDIA driver crashed VirtualBox
(see pitfalls below).

**Train data → model** (already done for experiment `20261009T145632Z`):
1. VM: `07-build-train-dataset.ps1` (20-min capture with `06-benign-workload.ps1`, convert, window, check).
2. Host: `encode_to_features.py` → `src/artifacts/features/self-dataset/`.
3. Host: `train_gnn_models.py` → `src/artifacts/runs/<exp>/`.

**Live demo:**
```
# host
uv run python src/live/live_server.py --bind 10.21.2.1 --run-dir src/artifacts/runs/exp01 \
  --scaler src/artifacts/features/self-dataset/feature_scaler.json \
  --experiment src/out/etw/experiments/20261009T145632Z/experiment.json
# VM, elevated PowerShell, two windows
C:\scripts\08-live-shipper.ps1        # capture + ship to the host
C:\scripts\06-benign-workload.ps1     # benign activity, the same as in training
```
Open http://10.21.2.1:8000. A window closes about 20 s after each 5-minute boundary. The first window
is `partial` (the capture started mid-window) and is not scored by design. Outputs go to
`src/live/sessions/<id>/` (gitignored).

## 4. Current state

**Verified**
- Benign training dataset: 20 minutes, 8 windows (5 min long, 2 min stride), 68,319 windowed edges,
  6,810 node-window copies (995 process / 2,558 file / 3,257 net).
- Valid model `runs/exp01`: embeddings not collapsed (`emb_std` 0.05–0.19), training flag rate ≈ 0.1 %.
- Live path end to end: converter hash matches training, the shipper excludes its own PID, and the
  first window was scored: 835 nodes, 19 flagged (2.3 %), all file nodes. 18 of the 19 are DLLs loaded
  once by a Google-updater-like process that never appears in training: benign novelty, not an attack.

**Runs but not validated**
- Replay equivalence (`compare_replay.py`, live path == offline pipeline) has not been run.
- Benign flag rate under `06-benign-workload.ps1` has not been measured. The window above came from running `03-workload.ps1`.

**Not done**
- Subgraph-score filter (SG-Det), labelled attack capture, test metrics, NODLINK W10 baseline,
  full Alg. 1 on GraphDB, LLM investigator.

## 5. Pitfalls we already hit

- **Wrong edges file.** `src/out/etw/SimulatedW10_train_edges.csv` is an old capture without windows.
  Pairing it with the windowed features gave all-zero features and a collapsed model. Always use the
  edges inside `experiments/<id>/`.
- **`03` vs `06`.** `03-workload.ps1` *installs* software (choco, configs). It is not a benign workload.
  Use `06` for benign tests, or the model will flag the installer activity.
- **VM "memory kill" that wasn't.** The VM died on a VMSVGA segfault in `libnvidia-glcore` (3D
  acceleration), not an OOM. Fixed with `accelerate3d off`. If it happens again, check `journalctl -k`
  and the VM's `Logs/VBox.log.1` before blaming RAM.
- **GraphDB memory.** `compose.yaml` sets an 8g Java heap but a 4g container limit. Lower
  `GRAPHDB_HEAP` to ≤ 3g before loading big graphs.
- **Restart the live server** after the VM dies: a dead connection can leave it without a listener.
- **Shipper copy.** After editing `src/live/etwship.py`, run `sync_ps1.py` so the copy embedded in
  `08-live-shipper.ps1` stays the same.

## 6. Open tasks you can pick up

1. **SG-Det** (next step): score each subgraph as the sum of its anomalous nodes' scores, map it to
   minor/moderate/significant/critical, and alert only on ≥ moderate (paper Alg. 1 lines 20–24, §7.2.1).
   The full spec is in `docs/PROGRESS.md` "Next 3 steps".
2. **More benign data:** 2–3 extra benign captures at different times, with background tasks triggered
   (Google updater, Defender scan), then retrain `runs/exp02` and calibrate τ on a held-out benign session.
3. **Replay check:** `src/live/README.md` step 4 → `ALL MATCH`.
4. **Labelled attack run:** one Atomic scenario during `06`, labels via `src/atomic-attack-pack/LABELING.md`.
5. **Clean-up:** rewrite or remove the stale root `README.md`.
