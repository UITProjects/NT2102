# PROGRESS (status as of 2026-10-22)

Replace lines in place when status changes; no history. "Verified" = checked in code/data.

## Current focus

The "widen benign + calibrate" cycle ran on **1 HOUR** of benign data (not 2): kfile-fixed capture
`x-20261022T140932Z` → `exp03` trained + promoted → live DVWA→RCE attack. Work is on branch
**`collect-benign-data-for-1-hour`**. Now: the LLM Attack Investigator (OpenAI-compatible) and the
residual browser false positive.

DONE this session (2026-10-22, verified live):
- **exp03** trained on the 1-h capture and **PROMOTED** (gate passed: 77-min kfile span, all 6 edge types
  non-zero, `emb_std ≠ 0` per type; Read 517,951 · Send 35,485 · Write 18,308 · Recv 10,109 · Load 4,474 ·
  Start 76). `ocr-live` restarted on exp03. The `0x380` kfile fix held at 1-h scale.
- **Live attack detected** (session `live-20261022T155035Z`): DVWA→RCE kill chain scored **37.76** then
  **56.0** (Moderate); kept subgraph = powershell cradle (powershell + .NET assembly loads + RAS/proxy
  DLLs). Non-browser benign stayed green (10.0 / 12.45 / 22.0, Minor). DEVIATIONS D8.
- **LLM investigator** (`alg2.py`, paper Alg. 2 l.3–15 incl. the serialize+reduce phase that collapses the
  DLL-load flood) already implemented; **added OpenAI-compatible support** (`--llm-api-key`, keyless-dummy
  for a custom `--llm-base-url`) across `alg2.py` / `engine.py` / `live_server.py`; selftest passes.

OPEN (the real limitation):
- **Browser benign FP**: an Edge-browsing benign window hit **31.0** (Moderate, false alarm). msedge's
  many per-instance processes are unseen in benign training → the *same* mechanism as the cradle. The fix
  is benign **variety** (training that covers browsing), **not** more capture time. DEVIATIONS D8,
  [[live-detector-benign-fp]].
- A **real LLM run** still needs the user's OpenAI-compatible endpoint URL + key; serialize/mock path
  verified without one.
- `06-benign-workload.ps1` **deadline-overrun bug** (the `while (now < deadline)` loop ran ~74 min for a
  60-min target because one scenario call blocked) — deferred, not yet debugged.

## Done

- Windows 10 ETW lab VM + collector + converter, end to end (Ansible, `src/ansible/`) + `src/scripts/06, 07`.
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
  `src/live/` (etwship.py shipper/replay, engine.py, live_server.py + UI, compare_replay.py). See `src/live/README.md`, DEVIATIONS D8.
- Live path end to end (verified): session `live-20261011T105718Z` showed converter sha256 `match`,
  dos_map `\device\harddiskvolume4 → c:` from the VM, shipper PID excluded. First scored window
  (`live-20261011T112141Z`, 11:25–11:30Z, VM running the old server-install workload): 7,813 edges, 835 nodes, 19 flagged
  (2.3 %), all FILE (445 file nodes; process 0/147, net 0/243). 18 of the 19 are syswow64 DLLs loaded once
  by a short-lived `updater` process (0 `updater` processes in training; the DLLs themselves are in
  training). Identical score 0.3220 vs τ_file 0.2297 (train max 0.2312), so prob = 1 each (clamped).
- Alg. 1 + SG-Det (code + selftest, verified on real data 2026-10-12): `src/pipeline/alg1.py` (in memory,
  [AUTHOR CODE] score = Σ clamped min-max prob, keep ≥ Moderate), used by `engine.py`; flags in
  `live_server.py`; UI shows level/score and greys subgraphs below the cut-off. The saved exp01 models
  reproduce `train_scores.csv` (max |diff| 1.3e-5), so the prob range is valid. Replay of
  `live-20261011T112141Z` through the live server: 11 scored windows, 5 with nodes above τ → 1 red
  (11:25 `updater`, 18 DLLs, score 18.0, Moderate). Other windows ≤ Minor (max 5.83). `alg1.py
  --session-dir` re-judges saved sessions without the VM.
- [OURS] live level scale (2026-10-13, DEVIATIONS D8): Moderate from sg_score 30 (`--sg-moderate-from`,
  [AUTHOR CODE] > 10). Re-judged replay: 11:25 `updater` = Minor → 0 red windows of 10.
- VM crash 2026-10-11 18:02 diagnosed (verified in `journalctl -k`, `VBox.log.1`): not OOM, a VirtualBox
  VMSVGA segfault in `libnvidia-glcore.so.595.84` (3D acceleration). Fixed: `accelerate3d off` on
  `w10-etw` and in `src/Vagrantfile`.
- LLM step after SG-Det (code + selftests, 2026-10-13): `src/pipeline/alg2.py`. [OURS] triage verdict
  attack / false_positive / uncertain, then the paper's Alg. 2 l.3–15 prompts for attacks (DEVIATIONS D9).
  Runs on its own thread in `engine.py`, `--llm-provider` in `live_server.py`, badge + report card in the UI.
  Plumbing verified with the keyword mock on a replay of `live-20261011T112141Z` (before the scale
  change; now needs `--sg-moderate-from 10.0001`): 1 red window (11:25 `updater`), verdict attached,
  `llm/` files written. Provider chosen (user, 2026-10-25): DeepSeek API `deepseek-flash`
  (`--llm-provider deepseek`, replaces the dropped Cloudflare attempt); code + offline selftest done.
  First real run (user-verified 2026-10-25): re-judged replay `live-20261011T112141Z` 11:25 `updater` window
  → `false_positive` (conf 0.88, 1 call, 3.1 s, 2159 tokens, default thinking). One benign window = one data
  point, not a rate (D9).
- Attack pack is now ONE attack: the RCE kill chain (`src/atomic-attack-pack/scenarios/rce-killchain.ps1`),
  delivered from a Kali attacker VM (`tools/launch_exploit.py`; local-only Vagrant box, pack mounted at
  `/opt/attack-pack`, not Ansible-managed). Kali exploits DVWA (OS command injection) and the RCE shell
  runs Discovery (T1033/T1082/T1057/T1016) → T1105 → T1053.005,
  self-logging labels (`LABELING.md`, D6). Launcher `--selftest` passes on the host; the old 01–10
  scenarios + orchestrator + local sink are removed.
- Windows collector provisioned + verified on `ws-etw` via Ansible (2026-10-18): `ansible-playbook
  windows.yml -e vulnlab_enabled=true` installs every tool — SilkETW/Python/converter/etwctl, Alloy,
  `start-live.ps1`, benign `06`/`07`, and DVWA. New `src/ansible/verify-windows.yml` asserts all
  present and DVWA serving on :3000. **Idempotent: a 2nd run is
  `changed=0`** (downloads now `force: no` + SilkETW/Python checksums pinned). DVWA (`win_vulnlab`):
  XAMPP (Apache + PHP + MariaDB) via Chocolatey, DVWA cloned to `C:\lab\DVWA` at the Apache DocumentRoot
  on :3000, Apache + MariaDB installed as services, and the schema built once via `setup.php`
  (admin/password, security=low).
- Alloy → Loki data path verified end to end on `ws-etw` → `ubuntu-trainer` (2026-10-18): a 37 s test
  capture shipped **losslessly** — Loki's per-provider counts equal the raw files (kproc 3372 / knet 223 /
  kfile 28224), with labels job/host/split/run/provider correct and line JSON intact. (The provisioning-time
  "Loki not reachable" warning was just Loki still initializing ~1 min after a trainer reboot; the trainer's
  host-only NIC also needed a `vagrant reload` once.)
- Log pipeline Alloy → Loki (code + selftests, 2026-10-17; Ubuntu side verified on `ubuntu-trainer`: lossless
  pull, host build = VM build, live via Loki = offline 8/8 windows; Windows side verified 2026-10-18:
  capture ships to Loki losslessly, counts match): `win_alloy` / `ubuntu_loki` roles, `src/pipeline/loki_pull.py` (interactive / flags) →
  `build_experiment.py`, `src/live/loki_source.py` (`live_server.py --loki-url`), `src/live/loki_push.py`
  (test feed). Lab moved to Windows Server 2022 + Ubuntu trainer (Ansible, `src/ansible/`).
- Improve loop (code + selftest, 2026-10-17): `lab` CLI (`src/lab/cli.py`): status / pull / train / loop /
  promote; one run folder per model; current-model pointer read by the live server, `serve.sh`,
  `rejudge.sh` and `ocr-live`.
- Concepts: provenance graphs; GNN message passing.
- kfile-truncation root cause + fix (2026-10-21, verified): the old captures' `kfile 1/21m` was a Kernel-File
  **FileName-rundown storm** — keyword `0x390` includes bit `0x10` (FILENAME), which makes ETW dump a Name
  event for every already-open file handle at trace start (~1.35M events all stamped at t0 on this host),
  starving the real-time consumer so genuine Create/Read/Write after the first minutes are lost. Fix: drop
  `0x10` → kfile keyword **`0x380`** (Create+Read+Write) in `etwlab.json.j2:15` + the VM's live `etwlab.json`.
  5-min smoke `x-20261021T164839Z`: kfile spans the full 5 min (`check.json` minutes 5.0), edge_types Read
  23309 / Write 1703 / Load 1675 / Send 3403 / Recv 2795 / Start 52 (all non-zero); converter resolves FILE
  names from Create events (721 FILE nodes). Cost = `unresolved Write 18090 / Read 10706` (files opened
  before t0, now dropped) — recorded in DEVIATIONS D2. Converter name resolution: `etw2nodlink.py:214-232`
  (`on_fcreate` fileobject→path; Read/Write resolve via `fobj` or `fkey`).
- Trainer VM synced to `main` (2026-10-22): `repo_branch` flipped `ansible-two-vm-provisioning → main`
  (`group_vars/all.yml:71`), `ansible-playbook -i inventory.local.ini ubuntu.yml` moved `/home/vagrant/NT2102`
  to main @ 64d1f0b (canonical `loki_pull.py`/`build_experiment.py`), `uv sync`, `ocr-live` restarted. exp02
  model + `current.local.json` pointer preserved (both untracked/gitignored).

## Invalid / needs redo

- (none)

## Not done

- 2h (or 90-min) benign train capture (07) on `ws-etw`, pulled from Loki, retrain into `exp03`, promote,
  calibrate, attack-test. NOT run yet; this is the paused cycle (see Current focus + Next 3 steps). The
  kfile-truncation blocker that made earlier captures FILE-starved is now fixed (see Done 2026-10-21).
  Still gate on `lab status` per-provider coverage (kfile ≈ full span) before trusting any new capture.

- Run the RCE kill chain end to end against DVWA. DVWA provisioning is verified on `ws-etw`
  (`ansible-playbook -i inventory.local.ini windows.yml -e vulnlab_enabled=true` serves DVWA on :3000,
  login admin/password, security low — see Done). Remaining: `vagrant up kali` (mounts the pack at
  `/opt/attack-pack`), then `python3 -I /opt/attack-pack/tools/launch_exploit.py --target
  http://10.21.2.10:3000` during a live session, and check the window reaches ≥ Moderate (sg_score ≥ 30)
  while benign stays green.
- Concepts 2 (rest) to 9. Phase 0 toy exercises; Phase 1 one-page paper summary + hyperparameter list.
- LLM container; hardware recorded.
- Replay equivalence check (`compare_replay.py`); user-confirmed runs of the selftests of
  score_window.py / engine.py / etwship.py; a benign live run with `06-benign-workload.ps1`.
- Test split capture, node labels, test scoring, NODLINK W10 detector baseline.
- LLM: DeepSeek on a red window from a real attack (the RCE kill chain live), to see the attack path
  (triage → IOCs → report). Alg. 2 l.16–25
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

Data validation (LLM input):
- NET attrs are SilkETW signed integers (`-1,707,025,385:-17663` = `23.220.64.154:443`); stored raw, decoded
  for display in the LLM document (`alg2.py`) and the live UI (raw shown beside it). Process attrs are only the image name (`updater`), no path or command line, so the
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
- `selftest.sh` skips `encode_to_features.py --selftest`.
- Runtime message names an old file: `07-build-train-dataset.ps1` ("05-collect-10-minutes-window.ps1").
- `src/Vagrantfile:13` names `./host-setup.sh`; the file is `src/setup-virtualbox.sh`.
- `live_server.py --sg-moderate-from` hard-codes 30.0 instead of `alg1.MODERATE_FROM`.
- Scenarios 05 (lsass handle access) and 09 (timestomp) may be invisible to the collector's file keywords
  (0x390 = name/create/read/write) [INFERENCE]; check on a capture.

Repo hygiene:
- `.gitmodules` still declares submodule `dataset/Simulated-Data` (PKU-ASAL), but `/dataset/` is
  gitignored and the data sits in `original-dataset/Simulated-Data`; fix or drop the entry.
- `src/out/etw/SimulatedW10_train_edges.csv` (old un-windowed capture) sits next to the experiment
  folders and is easy to pick by mistake (it caused the first, collapsed exp01 run).

## Next 3 steps (resume the paused cycle — full detail in `~/.claude/plans/i-want-you-to-splendid-naur.md`)

Pre-flight is already done (trainer on main + Loki ready + Kali selftest ok + kfile fix verified). Start here:

1. Launch the benign capture on `ws-etw` (VirtualBox window, elevated PS):
   `C:\scripts\07-build-train-dataset.ps1 -DurationMinutes 120 -Seed 42` (or 90). ~58 windows at 5-min/2-min.
   Note the `x-<UTC>` run stamp. (Runs in the interactive desktop only — WinRM/session-0 is refused and would
   mis-parent apps to powershell.)
2. On the trainer: `lab loop --latest --split train --lookback 1d --model exp03 --seeds 3 --yes`. GATE before
   promote: `lab status` shows kfile ≈ full span (fix holds at scale), all 6 edge types non-zero, `emb_std`
   ≠ 0 for every type. Then `lab promote exp03` + `sudo systemctl restart ocr-live`.
3. Honest calibrate + attack (both via `vagrant ssh` for the trainer/Kali; the Windows capture is manual):
   benign live session first (`start-live.ps1` + `06-benign-workload.ps1 -Seed 7`), record max benign
   `sg_score` (`alg1.py --session-dir`); set cut-off from benign only; THEN
   `vagrant ssh kali -c 'python3 -I /opt/attack-pack/tools/launch_exploit.py --target http://10.21.2.10:3000'`.
   Attack window must reach ≥ cut-off while benign stays green; confirm the kept subgraph is ATTACK indicators
   (httpd→cmd→powershell, tool images, attacker :8080), not NAT/powershell noise. Record in DEVIATIONS D8.

Watch item: the first 15-min `07` run (`x-20261021T161810Z`) did not write `stopped_utc` (capture left
running); the 5-min smoke stopped cleanly, so likely a one-off — but confirm `07` stops the service on the 2h run.
