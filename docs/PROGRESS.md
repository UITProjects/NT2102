# PROGRESS (status as of 2026-10-11)

Replace lines in place when status changes; no history. "Verified" = checked in code/data.

## Current focus

Live detection demo: the first live window is scored; benign-but-unseen activity is flagged (2.3 % of
nodes). Next: add the paper's subgraph-score filter (SG-Det), widen benign training data and calibrate
τ, then prove the live path matches the offline pipeline (replay check). In parallel: understand the
trainer (concepts 2 GCN, 3 RGCN, 4 one-class).

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
- Live path end to end (verified): session `live-20261011T105718Z` showed converter sha256 `match`,
  dos_map `\device\harddiskvolume4 → c:` from the VM, shipper PID excluded. First scored window
  (`live-20261011T112141Z`, 11:25–11:30Z, VM running `03-workload.ps1`): 7,813 edges, 835 nodes, 19 flagged
  (2.3 %), all FILE (445 file nodes; process 0/147, net 0/243). 18 of the 19 are syswow64 DLLs loaded once
  by a short-lived `updater` process (0 `updater` processes in training; the DLLs themselves are in
  training). Identical score 0.3220 vs τ_file 0.2297 (train max 0.2312). Under the paper's SG-Det levels
  the subgraph would be "minor" (18 × 0.322 = 5.80 < 10).
- VM crash 2026-10-11 18:02 diagnosed (verified in `journalctl -k`, `VBox.log.1`): not OOM, a VirtualBox
  VMSVGA segfault in `libnvidia-glcore.so.595.84` (3D acceleration). Fixed: `accelerate3d off` on
  `w10-etw` and in `src/Vagrantfile`.
- Attack pack + labelling scheme drafted: `src/atomic-attack-pack/` (10 scenarios, `LABELING.md`,
  `tools/build_node_labels.py`). Not yet run.
- Concepts: provenance graphs; GNN message passing.

## Invalid / needs redo

- (none)

## Not done

- Concepts 2 (rest) to 9. Phase 0 toy exercises; Phase 1 one-page paper summary + hyperparameter list.
- LLM container; hardware recorded.
- Replay equivalence check (`compare_replay.py`); user-confirmed runs of the selftests of
  score_window.py / engine.py / etwship.py; a benign live run with `06-benign-workload.ps1`.
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
- τ comes from training scores, so it sits at the training maximum (τ_file 0.2297 vs max 0.2312);
  any benign file in an unseen neighbourhood crosses it. Benign coverage is one 20-min scripted capture,
  so background jobs (e.g. the 32-bit `updater`) are unseen and get flagged.
- `03-workload.ps1` is a provisioner (choco installs, config edits), not a benign generator: use
  `06-benign-workload.ps1` for benign live tests.

Environment:
- `src/compose.yaml`: GraphDB heap `-Xmx8g` exceeds the container `mem_limit` 4g (latent; use ≤ 3g).

Repo hygiene:
- `.gitmodules` still declares submodule `dataset/Simulated-Data` (PKU-ASAL), but `/dataset/` is
  gitignored and the data sits in `original-dataset/Simulated-Data`; fix or drop the entry.
- `src/virtualbox-setup.sh` is a byte-identical copy of `src/setup-virtualbox.sh`; notebook
  `src/exploration/paper/window12-benign-dataset-exploration.ipynbwindow12-anomoly-dataset-exploration.ipynb`
  has a broken name. Remove / rename after confirming.
- `src/out/etw/SimulatedW10_train_edges.csv` (old un-windowed capture) sits next to the experiment
  folders and is easy to pick by mistake (it caused the first, collapsed exp01 run).

## Next 3 steps

1. SG-Det, the subgraph-score filter ([PAPER] Alg. 1 lines 20–24, §5.2, §7.2.1; Table 4), in
   `src/live/engine.py` (`alg1_lite` / `_score`):
   - per subgraph `sg_score` = Σ scores of its anomalous nodes [PAPER], plus `sg_margin` = Σ (score − τ)
     [OURS; NET scores can be negative]; levels minor < 10 ≤ moderate < 100 ≤ significant < 1000 ≤ critical;
   - base cut-off as a flag (`--sg-min`, default 10 as in the paper; [INFERENCE] not transferable to our
     score scale), later calibrated on benign sessions, never on the window being judged;
   - window `alert` only if ≥ 1 subgraph ≥ moderate; minor subgraphs stay in `subgraphs.json`, greyed in
     the UI; `windows.csv` gains `subgraphs_alert`; selftest case 18 × 0.322 → minor; update DEVIATIONS D8.
   - Accept: benign windows (with `06`) raise no ≥ moderate subgraph; the updater window becomes minor.
2. Widen benign training and calibrate τ: 2–3 benign captures at different times (trigger background
   tasks such as the Google updater and a Defender scan), retrain `runs/exp02`, set τ from a held-out
   benign session; report flagged nodes per window, mean ± std over ~10 benign windows.
3. Replay equivalence without the VM (`src/live/README.md` step 4, `--stride-min 2 --origin-ms
   1791557793343`); `compare_replay.py` must print `ALL MATCH`. After that, the end goal: run an Atomic
   scenario during the live demo and show its window raising a true alert (no metrics required).
