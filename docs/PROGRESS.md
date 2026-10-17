# PROGRESS (status as of 2026-10-17)

Replace lines in place when status changes; no history. "Verified" = checked in code/data.

## Current focus

Live detection demo: full Alg. 1 with the subgraph-score filter (SG-Det) now decides the window alert.
On the benign replay, 0 windows are red at our scale (≥ 30), but the 11:25 `updater` window is only hidden
(score 18), not fixed. Next: widen benign training data and calibrate, then prove the live path matches the offline pipeline (replay check), then run an Atomic
scenario live. In parallel: understand the trainer (concepts 2 GCN, 3 RGCN, 4 one-class) and Alg. 1.

## Done

- GraphDB environment: `src/graphdb/compose.yaml` (GraphDB 10.8.0, security on, `nt2102-net` 10.21.3.0/24).
- Windows 10 ETW lab VM + collector + converter, end to end: `src/legacy/vagrant-provisioners/01..05*.ps1` + `src/scripts/06, 07` (now Ansible, `src/ansible/`).
- Benign train dataset, experiment `20261009T145632Z` (verified, `src/out/etw/experiments/20261009T145632Z/`):
  20-min capture, workload seed 42, launch mode explorer-com; 8 windows (5 min / 2 min stride);
  37,294 edges in → 68,319 windowed edges (484 outside any window); 6,810 node-window copies
  (995 PROCESS / 2,558 FILE / 3,257 NET); `check.json` ok, all 6 edge types non-zero
  (Recv 27,503 · Load 24,831 · Send 13,900 · Write 954 · Start 645 · Read 486).
- Behavior features for that dataset: `src/artifacts/features/self-dataset/` (15 features, scaler fit on train).
- Behavior features for NODLINK SimulatedW10: `src/artifacts/features/paper/` (from `dataset/nodlink/SimulatedW10/`).
- Trainer code with selftest: `src/pipeline/train_gnn_models.py`.
- Valid OCRGCN training run `src/artifacts/runs/exp01` (verified; rerun 2026-10-11 on the matching
  experiment edges + features): no missing feature rows; split 5 train / 2 purged (w0005, w0006) /
  1 val (w0007); emb_std 0.05–0.19 for every type and seed; thresholds differ across seeds;
  train_flag_rate ≈ contamination (process 0.0017 = 1/575). Model saved from run 0 (seed 360).
- Live path code (with `--selftest` each): `src/pipeline/score_window.py` (shared scorer),
  `src/live/` (etwship.py shipper/replay, engine.py, live_server.py + UI, compare_replay.py),
  `src/legacy/vagrant-provisioners/08-live-shipper.ps1` (replaced by Alloy → Loki). See `src/live/README.md`, DEVIATIONS D8.
- Live path end to end (verified): session `live-20261011T105718Z` showed converter sha256 `match`,
  dos_map `\device\harddiskvolume4 → c:` from the VM, shipper PID excluded. First scored window
  (`live-20261011T112141Z`, 11:25–11:30Z, VM running `03-workload.ps1`): 7,813 edges, 835 nodes, 19 flagged
  (2.3 %), all FILE (445 file nodes; process 0/147, net 0/243). 18 of the 19 are syswow64 DLLs loaded once
  by a short-lived `updater` process (0 `updater` processes in training; the DLLs themselves are in
  training). Identical score 0.3220 vs τ_file 0.2297 (train max 0.2312), so prob = 1 each (clamped).
- Alg. 1 + SG-Det (code + selftest, verified on real data 2026-10-12): `src/pipeline/alg1.py` (in memory,
  [REPO] score = Σ clamped min-max prob, keep ≥ Moderate), used by `engine.py`; flags in
  `live_server.py`; UI shows level/score and greys subgraphs below the cut-off. The saved exp01 models
  reproduce `train_scores.csv` (max |diff| 1.3e-5), so the prob range is valid. Replay of
  `live-20261011T112141Z` through the live server: 11 scored windows, 5 with nodes above τ → 1 red
  (11:25 `updater`, 18 DLLs, score 18.0, Moderate). Other windows ≤ Minor (max 5.83). `alg1.py
  --session-dir` re-judges saved sessions without the VM.
- [OURS] live level scale (2026-10-13, DEVIATIONS D8): Moderate from sg_score 30 (`--sg-moderate-from`,
  [REPO] > 10). Re-judged replay: 11:25 `updater` = Minor → 0 red windows of 10.
- VM crash 2026-10-11 18:02 diagnosed (verified in `journalctl -k`, `VBox.log.1`): not OOM, a VirtualBox
  VMSVGA segfault in `libnvidia-glcore.so.595.84` (3D acceleration). Fixed: `accelerate3d off` on
  `w10-etw` and in `src/Vagrantfile`.
- LLM step after SG-Det (code + selftests, 2026-10-13): `src/pipeline/alg2.py`. [OURS] triage verdict
  attack / false_positive / uncertain, then the paper's Alg. 2 l.3–15 prompts for attacks (DEVIATIONS D9).
  Runs on its own thread in `engine.py`, `--llm-provider` in `live_server.py`, badge + report card in the UI.
  Plumbing verified with the keyword mock on a replay of `live-20261011T112141Z` (before the scale
  change; now needs `--sg-moderate-from 10.0001`): 1 red window (11:25 `updater`), verdict attached,
  `llm/` files written. No real LLM run yet (provider not chosen).
- Attack pack + labelling scheme drafted: `src/atomic-attack-pack/` (10 scenarios, `LABELING.md`,
  `tools/build_node_labels.py`). Not yet run.
- Log pipeline Alloy → Loki (code + selftests, 2026-10-17; Ubuntu side verified on `ubuntu-trainer`: lossless
  pull, host build = VM build, live via Loki = offline 8/8 windows; Windows side not yet run): `win_alloy` / `ubuntu_loki` roles, `src/pipeline/loki_pull.py` (interactive / flags) →
  `build_experiment.py`, `src/live/loki_source.py` (`live_server.py --loki-url`), `src/live/loki_push.py`
  (test feed). Lab moved to Windows Server 2022 + Ubuntu trainer (Ansible, `src/ansible/`).
- Improve loop (code + selftest, 2026-10-17): `lab` CLI (`src/lab/cli.py`): status / pull / train / loop /
  promote; one run folder per model; current-model pointer read by the live server, `serve.sh`,
  `rejudge.sh` and `ocr-live`. Legacy provisioners moved to `src/legacy/`.
- Concepts: provenance graphs; GNN message passing.

## Invalid / needs redo

- (none)

## Not done

- Data check (verified 2026-10-17): in capture `20261009T145632Z`, `kfile.json` only covers 14:56:33–14:57:40
  (67 s of a 20-min capture). FILE Read/Write edges come from that first minute. Check the next capture's
  per-provider time span before training on it.
- Windows side of Alloy → Loki on `ws-etw` (user, by hand: checklist in `src/ansible/README.md`), then a
  new benign train capture (07) pulled from Loki, retrain, and a new run replacing `exp01`.

- CVE → RCE demo scenario (target attack for the end goal): exploit a lab service (e.g. Apache /
  PostgreSQL installed by `03-workload.ps1`), then ATT&CK follow-on. The attack pack has only
  Atomic/custom scripts today.
- Concepts 2 (rest) to 9. Phase 0 toy exercises; Phase 1 one-page paper summary + hyperparameter list.
- LLM container; hardware recorded.
- Replay equivalence check (`compare_replay.py`); user-confirmed runs of the selftests of
  score_window.py / engine.py / etwship.py; a benign live run with `06-benign-workload.ps1`.
- Test split capture, node labels, test scoring, NODLINK W10 detector baseline.
- LLM: choose a provider (`uv sync --group llm`), re-judge the 11:25 window with
  `./rejudge.sh <session> <p> --sg-moderate-from 10.0001`, then one Atomic scenario live. Alg. 2 l.16–25
  not built.
- SG-Det cut-off not checked against an attack: our scale (Moderate ≥ 30 ≈ at least 30 anomalous nodes
  in one subgraph) was set from one benign window; a small Atomic scenario may be filtered out.

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
- `src/graphdb/compose.yaml`: GraphDB heap `-Xmx8g` exceeds the container `mem_limit` 4g (latent; use ≤ 3g).

Data validation (LLM input):
- NET attrs are SilkETW signed integers (`-1,707,025,385:-17663` = `23.220.64.154:443`); decoded only for
  display in `alg2.py`. Process attrs are only the image name (`updater`), no path or command line, so the
  LLM sees little context for processes.

Live tooling:
- `etwship.py --replay --speed 0` never advances the virtual clock (step = interval × speed = 0), so it
  hangs; at 600× each POST carries 10 min of events and the shipper times out. 120× works (52 min in 29 s).
- Replayed window 11:35 has 13,045 edges vs 12,516 live: the live run dropped late events (lateness 20 s).

Code bugs found in the 2026-10-13 docs pass (verified in code, not fixed yet):
- `serve.sh` help prints `sed -n '2,12p'`, one line too many (includes `set -euo pipefail`).
- `etwship.py --speed` help says "0 = no sleep", but 0 hangs (see Live tooling); default is 60, `replay.sh` uses 120.
- `build_node_labels.py` expects `node_id,type,name,timestamp`; the converter writes `node,node_type,node_attr`
  (no timestamp, types `PROCESS/FILE/NET`), and NET is checked as `network`. It cannot run as is.
- `attack-lib.ps1` / `cleanup-all.ps1` import `C:\AtomicRedTeam\invoke-atomicredteam\*.psd1`, but `02-tools.ps1`
  installs the module from PSGallery (`-Scope AllUsers`).
- `selftest.sh` skips `encode_to_features.py --selftest`; `w10_to_rdf.py` has no `--selftest`.
- Runtime messages name old files: `07-build-train-dataset.ps1` ("next w10_to_rdf.py", next is
  `encode_to_features.py`; "05-collect-10-minutes-window.ps1") and `02-tools.ps1` ("Run base.ps1 first").
- `02-tools.ps1` pins (`SilkZipSha256`, `InvokeArtVersion`, `AtomicsRef`) are still empty, so rebuilds
  install "latest"; copy them from `lab-versions.json`.
- `src/Vagrantfile:13` names `./host-setup.sh`; the file is `src/setup-virtualbox.sh`.
- `live_server.py --sg-moderate-from` hard-codes 30.0 instead of `alg1.MODERATE_FROM`.
- Scenarios 05 (lsass handle access) and 09 (timestomp) may be invisible to the collector's file keywords
  (0x390 = name/create/read/write) [INFERENCE]; check on a capture.

Repo hygiene:
- `.gitmodules` still declares submodule `dataset/Simulated-Data` (PKU-ASAL), but `/dataset/` is
  gitignored and the data sits in `original-dataset/Simulated-Data`; fix or drop the entry.
- `src/out/etw/SimulatedW10_train_edges.csv` (old un-windowed capture) sits next to the experiment
  folders and is easy to pick by mistake (it caused the first, collapsed exp01 run).

## Next 3 steps

1. Widen benign training and calibrate (the 11:25 `updater` false positive is hidden by the ≥ 30
   scale, not fixed; it needs coverage): 2–3 benign captures at different times (trigger background tasks such as the
   Google updater and a Defender scan), retrain into `src/artifacts/runs/exp02`. Run benign live sessions
   with `06-benign-workload.ps1` and re-judge them with `alg1.py --session-dir`; record max benign
   `sg_score` per window, mean ± std over ~10 windows.
2. Replay equivalence without the VM (`src/live/README.md` step 4, `--stride-min 2 --origin-ms
   1791557793343`); `compare_replay.py` must print `ALL MATCH`.
3. End goal: run one Atomic scenario during a live session. Check that its window reaches ≥ Moderate
   (sg_score ≥ 30). If not, the cut-off must stay above the benign max from step 1; record the outcome in
   DEVIATIONS D8 (decided on benign data, confirmed on the attack, not tuned to it).
