# PROGRESS (status as of 2026-10-11)

Replace lines in place when status changes; no history. "Verified" = checked in code/data.

## Current focus

Live detection demo: get the first `scored` live window and check benign flag rates, then prove the
live path matches the offline pipeline (replay check). In parallel: understand the trainer
(concepts 2 GCN, 3 RGCN, 4 one-class).

## Done

- GraphDB environment: `src/compose.yaml` (GraphDB 10.8.0, security on, `nt2102-net` 10.21.3.0/24).
- Windows 10 ETW lab VM + collector + converter, end to end: `src/scripts/01..07*.ps1`.
- Benign train dataset, experiment `20261009T145632Z` (verified, `src/out/etw/experiments/20261009T145632Z/`):
  20-min capture, workload seed 42, launch mode explorer-com; 8 windows (5 min / 2 min stride);
  37,294 edges in → 68,319 windowed edges (484 outside any window); 6,810 node-window copies
  (995 PROCESS / 2,558 FILE / 3,257 NET); `check.json` ok, all 6 edge types non-zero
  (Recv 27,503 · Load 24,831 · Send 13,900 · Write 954 · Start 645 · Read 486).
- Behavior features for that dataset: `src/features/self-dataset/` (15 features, scaler fit on train).
- Behavior features for NODLINK SimulatedW10: `src/features/paper/` (from `dataset/nodlink/SimulatedW10/`).
- Trainer code with selftest: `src/pipeline/train_gnn_models.py`.
- Valid OCRGCN training run `src/pipeline/runs/exp01` (verified; rerun 2026-10-11 on the matching
  experiment edges + features): no missing feature rows; split 5 train / 2 purged (w0005, w0006) /
  1 val (w0007); emb_std 0.05–0.19 for every type and seed; thresholds differ across seeds;
  train_flag_rate ≈ contamination (process 0.0017 = 1/575). Model saved from run 0 (seed 360).
- Live path code (with `--selftest` each): `src/pipeline/score_window.py` (shared scorer),
  `src/live/` (etwship.py shipper/replay, engine.py, live_server.py + UI, compare_replay.py),
  `src/scripts/08-live-shipper.ps1`. See `src/live/README.md`, DEVIATIONS D8.
- First live session reached the host (verified, `src/live/sessions/live-20261011T105718Z/`):
  converter sha256 `match` with training, dos_map `\device\harddiskvolume4 → c:` sent by the VM,
  shipper PID excluded; 1 window closed, `partial` (capture started mid-window, 8,842 edges), not scored.
- Attack pack + labelling scheme drafted: `src/atomic-attack-pack/` (10 scenarios, `LABELING.md`,
  `tools/build_node_labels.py`). Not yet run.
- Concepts: provenance graphs; GNN message passing.

## Invalid / needs redo

- (none)

## Not done

- Concepts 2 (rest) to 9. Phase 0 toy exercises; Phase 1 one-page paper summary + hyperparameter list.
- LLM container; hardware recorded.
- A `scored` live window; replay equivalence check (`compare_replay.py`); user-confirmed runs of the
  selftests of score_window.py / engine.py / etwship.py.
- Test split capture, node labels, test scoring, NODLINK W10 detector baseline, full subgraph
  construction (Alg. 1 with the subgraph-score filter; only Alg.1-lite exists), LLM investigator (Alg. 2).

## Open items

Data validation:
- Edge direction vs NODLINK; converter `excluded` / `unresolved` counts (2026-10-07 run: 761 unresolved
  Writes, 164 Reads; check the 10-09 summary in `converted/SimulatedW10_train_summary.json`).
- Verify ETW keyword bitmasks and event IDs against the provider manifest dumps.
- Short test captures to record: real JSON field names; whether Read/Write events carry a file name
  or only a file object; whether network events show PID 4 at top level; events/min per provider.
- Verify generator-launched apps have explorer as parent in converted edges.
- `build_node_labels.py`: fill in the TODO column names against real `*_node_attrs.csv`.

Model:
- Early stopping reacts to noise: benign val TNR at the 0.999 quantile is ~1.0, so one val node
  crossing τ starts the patience counter. Saved process model (seed 360) has `best_ep=3` (≈2 real
  steps after warmup); best_ep ranges 3–99 across seeds. Try `--patience 20` / compare seeds' flag rates.
- Reported `val_fpr = 0` is optimistic: the same validation split selects the epoch.

Repo hygiene:
- Root `README.md` and `src/README.md` describe an unrelated network-IDS lab; rewrite or remove.
- `src/out/etw/SimulatedW10_train_edges.csv` (old un-windowed capture) sits next to the experiment
  folders and is easy to pick by mistake (it caused the first, collapsed exp01 run).

## Next 3 steps

1. Keep the live session running past a 5-min boundary (with `06-benign-workload.ps1`); accept when a
   window shows `scored` and the benign flag rate per type is near the contamination rate (0.1 %).
2. Replay equivalence without the VM (`src/live/README.md` step 4, `--stride-min 2 --origin-ms
   1791557793343`), then `compare_replay.py` must print `ALL MATCH` (edges, features, scores).
3. Capture a labelled Atomic Red Team test split alongside the benign workload and score it with
   `score_window.py` (train scaler + `runs/exp01`); concepts 2–4 in parallel so the trainer is explainable.
