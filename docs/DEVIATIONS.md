# DEVIATIONS from OCR-APT (paper + upstream repo)

Each entry: what changed, why, expected impact, which claims it limits.
Tags: [PAPER] paper text · [AUTHOR CODE] verified in the authors' code (github.com/CoDS-GCS/OCR-APT) · [OURS] this project · [INFERENCE] unverified.

## D1. Windowed, near-live detection
- What: [PAPER] offline prediction over the whole test log. [OURS] 5-min windows (train: 2-min stride,
  8 windows over 20 min); every node is copied per window (`w0007_` prefix).
- Why: the live demo needs bounded, incremental scoring.
- Impact: nodes whose behaviour spans windows are seen in fragments; idle-time stats are capped by
  the window length (train max idle = 290 s, `src/artifacts/features/self-dataset/feature_scaler.json`).
- Limits: no claim about whole-log detection quality.

## D2. Own Windows ETW data; benign = idle, pulled from Loki
- What: [PAPER] NODLINK / DARPA TC3 / OpTC. [OURS] ETW Kernel-Process/File/Network on a Windows VM (SilkETW
  until 2026-10-26, then our own collector, D10), converted to the NODLINK SimulatedW10 schema.
  Benign = **idle machine activity**: no capture script at all — the always-on live capture ships idle
  background activity to Loki, and a clean idle time-range is pulled straight from Loki
  (`lab pull --start <t> --end <t> --split test`) and trained on (`lab train --benign-split test`). There is
  no `src/scripts/` capture script. The benign label of such a range is an **operator assertion** (no attack
  was run in it), recorded with the range in the model's `lineage.json` (`benign_split`, `label_source`).
- Why: simplest possible concept demo. Against an idle baseline the attack's whole discovery+RCE burst is
  out-of-distribution, so it separates cleanly (idle = green, attack = red).
- History (2026-10-31), two failure modes before this: (1) a **seeded scripted workload**
  (`06-benign-workload.ps1`: netcheck/download/files ran ipconfig/curl/cmd/net — the attack's own
  living-off-the-land binaries) taught the model the attack's tools, so on exp04 every attack process scored
  below τ_process (−0.400) and the attack did **not** alert. (2) A natural manual capture via a rewritten
  `07` was tried, then both `06` and `07` were **removed** for the idle-from-Loki approach. exp05 (idle)
  result: idle windows green (≤ Minor 8.0, no alert), the live DVWA→RCE attack **red** (subgraph 106.96,
  Significant, alert True; flagged the attacker stage IP, `downloaded_tool.bin`, the RCE powershell), LLM
  triage `attack` (0.85) + report. [[live-detector-benign-fp]].
- Impact: an **idle baseline is thin** — the idle set that worked was ~16 min with only 6 net / 13 process
  training nodes, so the per-node net threshold is noisy (`val_fpr=1.0`); the per-subgraph SG-Det cut-off
  (Moderate ≥ 30 until 2026-11-03, the paper's ≥ 10 since; D8) absorbs that noise, keeping idle windows green, but **any real user activity in a benign
  window can raise false positives**, so the demo must keep benign windows idle. Not a general detector —
  a concept demo baseline. Converter still drops/unresolves some events (see converter `_summary.json`).
- 2-day baseline (2026-11-04, exp07): one long idle range minus excluded windows instead of one short clean range.
  `lab train --exclude-file` drops every window overlapping a listed range (`src/artifacts/exclusions/`): the attack, RDP
  sessions, and the 6-h rotation canary (`etwctl.ps1` runs `cmd /c whoami`). 174 of 1,415 windows, about 41 h kept. Why: a 3 h
  range misses daily and ~10 h OS jobs, which were 11 of exp06's 16 windows ≥ 10. Impact: idle reds on held-out windows 4 → 0
  (3 seeds, 200 windows); interactive sessions stay red, as an idle model should. `powershell.exe` stays in every window,
  because the 5-min `etwlab-live` watchdog starts it. Details: `docs/notes/retrain-2day-fp-lesson.md`.
- Logged-on session (2026-11-09, exp08): an RDP connect + WinRM at 10:30Z and an interactive logon at 10:46Z on `ws-etw-0`
  raised 8 benign alerts in 4 h (servicing after WinRM, `ping`/`ipconfig` in the session, then `dsregcmd.exe` hourly while the
  session stays logged on); all 7 LLM-judged were `false_positive`. Impact: idle means no one logged on. Sign out after RDP, and
  exclude logon → sign-out from training. A retrain does not fix this. Loki retention raised 72 h → 90 d so a baseline can cover
  more than 3 days. Details: `docs/notes/logged-on-session-fp-lesson.md`.
- `ws-etw-1` is **not idle-only** (user, 2026-11-12, exp15-ws1): its baseline 11-06 20:30Z → 11-12 12:15Z *includes* the
  teammate's setup and admin work (account creation, `icacls`, git clone, Node/npm installs, Edge) and **all their RDP sessions**,
  plus the VM's current state (react2shell `node` server, a SYSTEM task running `powershell → whoami` every minute, the NetBIOS
  broadcast). Excluded only: our 6-h rotation canaries and the teammate's exploit / enumeration runs (react2shell RCE children of
  `node.exe`, winPEAS, `systeminfo`, `netsh`, `runas`+`whoami` privesc tests, one ambiguous `icacls` ×8 burst): 214 of 4,070 windows,
  `src/artifacts/exclusions/ws-etw-1-20261112.json`. Why: the user's choice, so the model matches how `ws-etw-1` is really used.
  Impact: on `ws-etw-1` a human RDP session and the teammate's tools used for setup (`net user`, `icacls`, `runas`) are normal, so
  they will not raise alerts there (and, since `exp16-ws0`, on `ws-etw-0` too — see next bullet); `whoami` under `powershell` is normal too.
- `ws-etw-0` is **now a Domain Controller and no longer idle-only** (user, 2026-11-12, exp16-ws0): it was promoted to the `nt2102.lab` DC
  at 11-09 10:41Z (verified by Loki + WinRM), so its baseline 11-09 16:49Z → 11-12 15:08Z *includes* all DC steady-state work
  (ADWS/DFSR/DNS/NTDS, replication) **and admin RDP/console sessions**, excluding only our rotation canaries and the 11-10 14:49–15:05Z
  DVWA→RCE kill chain (`src/artifacts/exclusions/ws-etw-0-20261112.json`, 67 of 2107 windows). This **supersedes the logged-on-session
  bullet above for the live model**: an admin session on `ws-etw-0` is now learned as normal, so — like `ws-etw-1` — a hands-on-keyboard
  attacker who looks like the admin would blend in, and no such detection is claimed. `exp10` (idle, pre-DC) is retired from live use.
- Limits: our numbers are not comparable with the paper's tables; no detection-quality claim.

## D3. Feature extraction re-implemented (`src/pipeline/encode_to_features.py`)
- What: [AUTHOR CODE] `encode_to_PyG.py::feature_engineering` drops all-zero columns and fits the idle
  MinMax scaler on train+test. [OURS] all 15 columns kept; scaler fit on train only and reused;
  no RDF/GraphDB/PyG step; repo rounding of idle gaps to whole seconds kept (switchable).
- Why: fixed feature dimension across train/test/live; no test leakage into scaling; CSV for EDA.
- Impact: test/live idle values may fall outside [0, 1]; overlapping windows produce correlated rows.
- Limits: feature values are not bit-identical to the repo's.

## D4. No RDF / GraphDB
- What: [AUTHOR CODE] `transform_to_RDF.py` writes the graph as Turtle into GraphDB; Alg. 1 and Alg. 2 l.16–25
  query it with SPARQL. [OURS] no RDF and no GraphDB: edges stay CSV, Alg. 1 runs in memory per window (D8).
- Why: a 5-min window fits in RAM; one less service to run.
- Impact: none on the detector.
- Limits: no claim about the repo's GraphDB-based Alg. 1 / Alg. 2 l.16–25 path.

## D5. Trainer re-implemented (`src/pipeline/train_gnn_models.py`)
- What: [AUTHOR CODE] authors' PyGOD fork (`OCRGCN`), random 15 % validation, stop at first TNR decline,
  random seeds after run 0. [OURS] own `OCRGCN` (PyG `RGCNConv` ×3, 32-d, lr 0.005, β 0.5, warmup 2,
  contamination 0.001); validation = last window held out + 2 overlapping windows purged; patience 5;
  seeds 360 + run; `use_deterministic_algorithms(warn_only=True)`. For scoring test/live graphs,
  `build_graph(..., relations=<train_meta.json relations>)` reuses the training relation ids and drops
  edges whose relation was never seen in training (counted), instead of re-deriving ids from the data.
- [OURS] exp07 (2026-11-04): `--val-windows 60` (2 h tail; exp06's single window gave NET `n_val=0`) and `--tnr-tolerance 0.01`:
  a validation TNR within 0.01 of its peak counts as a tie, so the later epoch is kept. [PAPER] §5.1.2 stops when TNR declines;
  with a large validation set that keeps a barely trained model (FILE `best_ep=4`, τ +4.94 vs −0.18), because a perfect TNR
  only exists before the sphere tightens. Default 0 keeps the earlier behaviour. Set on validation data only. Accepts up to
  1 % validation FPR at the chosen epoch (exp07: 0.3 % FILE, 0 NET, 0 PROCESS).
- Why: explainable code without the fork; avoid leakage between overlapping windows.
- Impact: [INFERENCE] centre/radius update and loss follow upstream PyGOD OCGNN, not verified against
  the fork; results may differ from the repo's.
- Limits: cannot claim to reproduce OCRGCN exactly.

## D6. Labels from injected attacks (`src/atomic-attack-pack/LABELING.md`)
- What: [PAPER] DARPA ground truth / analyst reports (with §7 label-provenance caveats). [OURS] the
  attack self-logs a JSONL; a node is malicious if it matches an indicator AND is active in the
  attack time range.
- [OURS] the injected attack is now ONE RCE kill chain (`scenarios/rce-killchain.ps1`), delivered from a
  Kali attacker VM (`tools/launch_exploit.py`): Kali exploits DVWA via its documented OS-command-injection
  page (T1190/T1059.001), then the RCE shell runs Discovery (T1033/T1082/T1057/T1016) → loud network scan
  (T1046) → ingress transfer + tool execution (T1105/T1569.002) → read-only MySQL enumeration/dump
  (T1552/T1213) → C2 beacon (T1071) → persistence (T1053.005) → defense evasion / anti-forensics (T1070.001
  clear the Security/System/Application event logs via `wevtutil.exe`, T1070.004 delete the downloaded
  ingress tools). The external origin means the attack subtree is rooted at the vuln service
  (`httpd.exe → cmd.exe → powershell → …`), which is itself the anomalous signal; indicators are the tool
  image names, the downloaded files, the `UpdaterSync` task, `wevtutil.exe` + the `.evtx` logs it clears,
  and `<attacker>:8080`. Clearing the event logs is observable but does **not** disturb collection
  (`etwcollect` reads live ETW real-time sessions, not the `.evtx` event logs). **Observed on the
  2026-11-06 live run** (window `20261106T153500Z`, exp08, paper-faithful SG-Det Moderate=10): the window
  alerted **RED** (sg 54.42, Moderate, 1 kept subgraph) with LLM triage **`attack` 0.9**; the kept
  subgraph held the real C2 (`10.21.4.30:8080`), the `mysql.exe` dump, and the executed tools
  (PsExec/PSEXESVC, rclone, nmap, ncat). The T1070 phase ran to completion (T1070.001 + T1070.004 logged
  last) and its `wevtutil.exe` process + file nodes were captured, but they scored **in-distribution**
  (flagged=False) and did not enter the kept subgraph — a lone short-lived process is not anomalous by its
  own per-node behaviour counts (the anomaly is its parentage, unencoded; D5/D8). So T1070 adds observable
  telemetry without lifting the alert, which rests on the tool/C2/DB nodes. DVWA's command-injection page (`/vulnerabilities/exec/`, security=low) is a real,
  documented and intentional OS-command sink, so the exploited RCE is faithful. (Earlier: 10 Atomic/custom
  scenarios run on the VM.)
- [OURS] attack realism, to raise the signal **honestly** (not by detector tuning): the chain is
  deliberately high-volume (user, 2026-11-04) and the **T1105 phase downloads and EXECUTES real benign
  signed tools** — Sysinternals **PsExec** (as SYSTEM: `PsExec64.exe → PSEXESVC.exe → cmd.exe → whoami`)
  and **rclone** (`copyurl` fetch from the C2), with **nmap/ncat best-effort** (staged on Kali by
  `roles/kali_attack`, default-off, falling back to the PowerShell TcpClient sweep). Rationale: the graph
  keys FILE nodes by path-hash and NET nodes by `ip:port`-hash (CLAUDE.md §9) and uses **no content
  feature**, so real binaries on disk add nothing over the earlier inert markers — the added signal is the
  tool's **process + socket nodes** (unseen in the idle baseline), which only appear when the tool is
  *executed*. Lab-only and benign: real but non-malicious tools, read-only DB, isolated host-only subnets,
  Defender off, disposable VM; no mimikatz / credential-theft malware. Each exec is timeout-guarded
  (`Invoke-ToolWithTimeout`) and skipped unless a real PE was delivered, so the chain never hangs. The
  detector stays **paper-faithful SG-Det** — an earlier `--sg-net-weight` NET ×2 lever was **rolled back**
  (2026-11-04); the Moderate=10 threshold is unchanged and the live result is reported as observed.
- Why: exact knowledge of what was run, no external ground truth available.
- Impact: weak oracle; benign processes with the same image name inside an attack window can be
  over-labelled.
- Limits: claims only "detection of injected ATT&CK techniques on our capture".

## D7. Environment
- What: [OURS] Python deps via uv (`uv.lock`), torch / torch-geometric, no PyGOD; Loki + Grafana in
  Docker on the trainer.
- Impact: none expected for the detector.

## D8. Live inference path (`src/live/`, `src/pipeline/score_window.py`, `src/pipeline/alg1.py`)
- What: [PAPER] offline prediction on the whole test graph (§5.1.3), then Alg. 1 subgraphs with a
  subgraph anomaly-score filter (§5.2). [OURS]:
  - The VM's raw SilkETW lines reach the host, which reorders them by event time (20 s lateness), runs
    the training converter (`src/pipeline/etw2nodlink.py`, same sha256), cuts clock-aligned tumbling
    5-min windows, and scores each window with the frozen train scaler, train relation list and per-type
    models.
  - Transport (user decision, 2026-10-17): Grafana Alloy on the VM ships every line, unchanged, to Loki
    on the trainer. `live_server.py --loki-url` follows the newest test run there
    (`src/live/loki_source.py`). Training pulls the same store (`loki_pull.py` → `build_experiment.py`).
    The HTTP shipper (`etwship.py`) remains only for `--replay` / `compare_replay.py`.
  - Clock with Loki: no VM heartbeat travels through Loki. The engine clock is the newest event time
    seen. After `--loki-idle-s` without new lines (default 2 × lateness), it follows the host's wall
    clock minus the last measured lag. The shipper path used the VM wall clock instead. Windows close
    about 1–5 s later: Alloy batches up to 1 s, then the poll interval adds 1 s. The UI's "clock skew"
    tile becomes "pipeline lag (host − newest event)".
  - Alloy's own file reads appear in Kernel-File. Their edges are dropped by image name (`alloy`, like
    `silkservice`), in training and live alike. Raw lines stay complete.
  - Windows starting before the capture are `partial` and not scored. The shipper's own PID is dropped.
    Relations unseen in training are dropped (counted). No cross-window dedup. State is in memory.
- Alg. 1 per window (`alg1.py`, in memory, no GraphDB) follows [AUTHOR CODE] `detect_anomalous_subgraphs.py`:
  initSG = edges touching an anomalous node; top 15 seeds per type; 1-hop forward/backward traversal to
  unvisited anomalous nodes; ≥ 3 nodes; each node correlated once; Louvain above 5000 edges; identical
  subgraphs removed.
  - Subgraph score = Σ over anomalous nodes of prob = clamp((score − train_min_t) / (train_max_t −
    train_min_t), 0, 1). [AUTHOR CODE] definition (`subgraph_anomaly_score` sums PyGOD
    `Prediction_probability`, 'linear'); [PAPER] §5.2 only says "sum of the scores", but raw scores
    differ in scale per type (NET is negative). train_min/max from `train_scores.csv` (split = train);
    the saved model reproduces that file to 1.3e-5.
  - Level scale = [PAPER] §7.2.1 since 2026-11-03 (user decision: "exactly the paper"): ≤ 1 Negligible
    ([AUTHOR CODE] only), < 10 Minor, 10–100 Moderate, ≤ 1000 Significant, else Critical. A score of exactly 10 is
    Moderate as the paper text reads ("between 10 and 100"); [AUTHOR CODE] l.394 says `<= 10` Minor, so the two
    differ only at 10.0 (clamped probs often sum to whole numbers). `--sg-moderate-from` (default 10; `10.0001` =
    [AUTHOR CODE]; `30` = the earlier [OURS] scale, 2026-10-13 → 2026-11-03). `ocr-live` gets it from Ansible
    `live_sg_moderate_from` (default 10). A window alerts only if ≥ 1 subgraph is ≥ Moderate.
    Impact measured on the trainer before the switch (exp06, 193 scored idle windows 2026-11-02T23:14Z →
    11-03T15:25Z): **6 red at ≥ 10 vs 1 red at ≥ 30** (16, 25, 13, 12, 15 + the 129.0 Significant window that is
    red on both). The exp06 attack windows of 2026-11-02 (max 9.0 / 2.0) stay **Minor (green) at 10 as well**.
    More red windows also use more of the daily LLM budget (`--llm-max-per-day`, D9).
  - [OURS] no top-k fallback when nothing reaches Moderate ([AUTHOR CODE] l.433–461); live, it would make every
    window red.
  - [OURS] a partition still above max_e is kept whole (`oversize`), not sampled per edge type.
  - [OURS] optional numeric cut-off `--sg-min-score`, to be calibrated on benign sessions only.
  - [OURS] the anomalous set is capped at the 300 highest margins (UI guard).
- Why: live demo, with the same code path as offline so a replay of the training capture can be checked
  against the offline artefacts (`compare_replay.py`). Level scale history: [PAPER] §5.2 says the subgraph set
  "can be adjusted based on the desired abnormality threshold". The earlier [OURS] 30 was set because a 5-min
  window is far smaller than the paper's whole-log graphs and the [AUTHOR CODE] cut-off (> 10) left the benign
  11:25 `updater` window red (30 = its score 18 plus margin). On 2026-11-03 the user chose the paper's own
  scale instead: no deviation to defend, at the cost of more benign red windows (measured above). The points
  below about "30" describe that earlier scale and the results it was measured with.
- Impact:
  - Loki clock [INFERENCE]: while a capture runs, SilkETW emits kernel events continuously, so the event
    clock rarely stalls. When the capture stops, the last full window closes through the idle fallback.
    If the events stall but the capture keeps running, windows close up to `--loki-idle-s` late. They
    are not lost. Not yet measured on the VM.
  - Events later than the lateness are dropped (shown in the UI); processes started before a window
    have no Start edge inside it.
  - Since prob ≤ 1, ≥ 30 means "at least ~30 anomalous nodes in one connected story" (paper: > ~10).
    [INFERENCE] the paper's levels were set for DARPA-scale graphs, not 5-min windows (~100–850 nodes).
  - Benign replay `live-20261011T112141Z`: 5 windows with ≥ 1 node above τ; [AUTHOR CODE] scale → 1 red
    (11:25 `updater`, 18 unseen-context DLLs, score 18); our scale → 0 red.
  - An attack whose best subgraph scores < 30 in a window is filtered out. The attack side is not
    measured; 30 comes from ONE benign window. Check the Atomic / demo attack against 30, do not re-tune
    on the attack window (that fits the threshold to the test).
  - The scale hides the updater false positive, it does not fix it (≥ 30 unseen DLLs would be red
    again). Wider benign training (PROGRESS) is the real fix.
  - Calibration + attack result (2026-10-22, `exp03` on the 1-h kfile-fixed capture `x-20261022T140932Z`,
    live session `live-20261022T155035Z`): a live DVWA→RCE kill chain scored **37.76** then **56.0**
    (Moderate) in its window; the kept subgraph is the powershell cradle (powershell ×2 + ~26 .NET/
    PowerShell assembly loads + RAS/proxy DLLs). Non-browser benign stayed **≤ 22** (Minor, green), so the
    30 cut-off separated them. BUT a benign window with **Edge browsing hit 31.0** (Moderate, false alarm):
    msedge spawns many short-lived processes, each a fresh node under PID+start-time keying (D4) and so
    unseen in benign training, tripping the *same* "unfamiliar process+DLL burst" mechanism as the cradle.
    This is a benign-**variety** gap, not a duration gap — more minutes of the same seeded workload do not
    fix it; the fix is benign training that covers browsing. Reading at the time: detection works, but the
    attack/benign margin is narrow (56 vs 22 without the browser; 37.76 vs 31 with it) and both sides are
    the same mechanism, so 30 is not yet a trustworthy clean separator. [[live-detector-benign-fp]]
    **Superseded by the correction below.**
  - **Correction (2026-10-25, verified): live windows have no file events, so no live result so far is a
    valid detection result.** Every window of `live-20261022T155035Z` holds **zero Read/Write edges**
    (exp03 training windows: ~14,000 Read + ~500 Write each). Cause, measured on `ws-etw`: SilkService
    writes Kernel-File (`kfile`) events far behind real time. From a fresh run it falls behind at once
    (event time advances at ~20–25 % of wall time; 9+ min behind after 25 min) with SilkService at ~1 CPU
    core, and nearly all recent kfile events are SilkService's own writes to `kfile.json` plus Alloy's reads
    of it (a self-feeding loop). Alloy and Loki pass the delay through (both ~real time for kproc/knet),
    and the engine drops lines older than the watermark (newest event − 20 s) as late (`late_dropped`
    ≈ all kfile lines). ETW reports `Buffers Lost: 0`, yet SilkService memory stays flat (~275 MB), so where
    the backlog goes (and whether kfile events are also lost) is open [INFERENCE: lost at the real-time
    layer]. Not explained yet: why the Oct 22 1-h training capture kept a full 77-min kfile span.
    Rerun 2026-10-25 (`live-20261025T073042Z`, exp03, attack 14:45–14:47 local): the attack's processes
    (httpd, cmd, powershell ×3, systeminfo, PING; images whoami/tasklist/ipconfig/net/schtasks) all reached
    the graph, but **none was flagged** (closest: the cradle powershell, 0.004 below τ). The window went red
    (33.87, Moderate) only because of a benign msedge launch at 14:49. Benign 14:35 / 14:40 stayed green
    (12.0 / 21.0). A laptop replay of that session, which receives the kfile lines live dropped, scored the
    14:35 benign window 3.0 instead of 12.0: one data point consistent with "missing file actions push live
    features off the training distribution" [INFERENCE], not proof. Until the collector keeps up with kfile
    (lag < lateness), live detection is unvalidated.
  - **Cause found (2026-10-26, SilkService source):** SilkService writes every event with
    `File.AppendAllText` (open + write + close per line), so each line adds ≥ 1 Create + ≥ 1 Write of its
    own to Kernel-File; Alloy's reads add more. In `live-20261025T073042Z`, 224k of the first 300k kfile
    lines are SilkService's and 45k Alloy's. Fix: our own collector (D10).
  - **NET attribution bug (verified 2026-10-26, `live-20261025T073042Z/raw/knet.json`):** in 77 % of knet
    events (14,826 / 19,212) the header PID differs from the payload PID (header = Idle 0, System,
    SilkService; payload = msedge, …). SilkETW stores the payload owner as `XmlEventData._PID` and the header
    copy as `PID`; `etw2nodlink._net()` reads `pid`, so most NET edges of every capture so far (exp01–exp03
    included) hang off the wrong process. D10 data writes the payload PID as `PID`; the converter will
    prefer `_pid` so old captures are fixed on re-conversion.
- 24/7 operation (2026-11-02, [OURS] engineering, not in the paper): bounded Loki catch-up (≤ 20 000 lines per
  provider per poll; the clock is held at the cursor of a provider that is behind, so no line is dropped as late
  by the slicing), a restart mid-run writes a new `<run>.rN` session folder (never a second raw copy into the
  old one), a converter error skips that one event (counted as `bad_events`), and a watchdog exits for systemd
  to restart on a dead worker thread / hung poll. Impact on scoring: none on normal data; a skipped event is an
  edge missing from its window (visible as a count).
- Limits: shows near-live node scoring + Alg. 1 on our own data. No detection-quality claim until windows
  are labelled (D6); no claim about Alg. 1 precision. The cut-off is the paper's (Moderate ≥ 10, §7.2.1) since
  2026-11-03; the paper set it on DARPA-scale whole-log graphs, not 5-min windows, so it is not calibrated for
  our window size [INFERENCE].
- Persistent window history (2026-11-03, [OURS] engineering, `src/live/history.py`): every window state is appended
  to `sessions/history.jsonl` and each scored window's UI detail to `windows/<wid>/detail.json`; `GET /api/history`
  (range `from`/`to`, `alert`, `replay`) and the UI's History panel query them across restarts and session
  rotations. A restart's `.rN` re-read duplicates up to 1 h of windows: the query shows one per window start
  (scored beats partial, then the later restart). A row keeps the alert and verdict **as judged at the time**,
  with the cut-off then in force (`moderate_from`), so windows judged at 30 are not silently re-labelled at 10
  (re-judge offline with `rejudge.sh` for that). Retention: only `<session>/raw/` is pruned (after
  `live_raw_keep_hours`, 12); window results are kept. Sessions written before the file existed are backfilled
  once from `windows.csv` + `llm_verdicts.csv` (no LLM report text for those). Impact on scoring: none.
- **Subgraph score counts flagged nodes, not attack severity (2026-11-05, verified):** `alg1.py:170`
  `sg_score = Σ prob over the subgraph's flagged nodes`, `prob = clamp((score − train_min)/(train_max −
  train_min), 0, 1)` ([AUTHOR CODE]). Each flagged node adds up to 1.0, so the score tracks the **number** of
  flagged nodes, not how anomalous the attack is. A large benign cluster (Windows servicing manifest reads;
  our own watchdog's .NET/module startup reads) sums past Moderate and outranks the small attack cluster.
  Measured: in `sessions/history.jsonl` every alerting window is an LLM-confirmed false positive **except**
  the 2026-11-05 00:50Z attack, which had the fewest flagged nodes (18) but the right ones ([OURS]/[INFERENCE]
  consequence). See `docs/notes/subgraph-score-fragility.md`.
- **Attack detection rests on one context-sensitive NET node (2026-11-05, verified):** the attack reaches
  Moderate only when the C2 beacon socket is flagged and seeds a subgraph pulling in the attack nodes. Same
  exp07, same τ, the same socket (`10.21.4.30:8080`, near-identical own features) scored **+1.165 (flagged)**
  at 00:50Z but **−1.50 (in-distribution)** at 15:20Z, because the per-node score is a 3-hop `RGCNConv`
  embedding (D5) that rides the whole window's graph context, and no feature encodes peer-IP novelty. [OURS].
- **[OURS] watchdog (own-tooling) exclusion (2026-11-05):** kernel ETW carries no command line, so our
  always-on live-capture watchdog `powershell.exe` is indistinguishable from any other by image name. It is
  the one process that reads our own `\start-live.ps1` / `c:\programdata\etwlab\*`, so
  `etw2nodlink.own_tooling_pids` / `filter_own_tooling_csv` drop it (nodes + edges) from BOTH training
  (`build_experiment.py`, before windowing) and live scoring (`engine._score`). The attack's RCE PowerShell
  never reads those paths, so it is kept. Impact: removes the watchdog from the baseline and from live windows;
  does not address non-watchdog FPs (e.g. TiWorker servicing — addressed by a longer baseline).
- **[OURS] NET alert rule (user, 2026-11-09; `alg1.REQUIRE_TYPES`, `--sg-require-types`):** a level-kept (≥ Moderate)
  subgraph is alerted, and so sent to the LLM (daily budget unchanged), only if it holds ≥ 1 anomalous NET node. Otherwise
  it stays in the output with `kept = False` and `suppressed = "missing anomalous node type(s): net"`. The window is
  normal, the engine logs the reason, `history.jsonl` keeps `subgraphs_suppressed` / `suppressed_reason`, and the LLM never
  sees it. [PAPER] / [AUTHOR CODE] keep on the level alone; `--sg-require-types none` restores that.
  Why: the benign false alarms so far were file-only (the nightly 22:25 powershell) or file + process (`dsregcmd`,
  servicing, Defender), while the DVWA→RCE chain touches the network in each of its windows (httpd recv, scan, download, C2).
  Impact, re-judged offline with exp08 and today's filters over the 10 sessions that held an alert since 11-05: alert
  windows 19 → 8, **attack windows 5/5 kept**, **false alarms 14 → 3**. The 3 left are human activity that touches the
  network (11-06 15:50 Ansible, 11-09 10:30 and 10:45 RDP/WinRM session).
  History: the first version required file + net + process. It lost the 11-06 00:50Z C2-beacon window (net + process; its
  live file node was our own `c:\lab-labels` file, dropped since D9). The user's "NET or > 10 anomalous nodes" filtered
  nothing: sg_score ≥ 10 already forces ≥ 10 anomalous nodes, because each prob ≤ 1.
  Claims limited: the rule was chosen on the same history it was checked on (in-sample, one kill chain). An attack window
  with no anomalous NET node is missed (local-only steps such as persistence or cleanup in a window of their own). Whether a
  window alerts now rides on the NET model's τ, the thinnest of the three models (D2), so a retrain can move it.

## D9. LLM step after SG-Det: triage + partial Alg. 2 (`src/pipeline/alg2.py`)
- What: [PAPER] Alg. 2 (§6, App. B) turns every anomalous subgraph into an attack report. It never
  decides attack vs benign, and its instructions ("APT attack investigator … summarizing … into attack
  reports") assume an attack. [OURS] for each red window, a neutral triage prompt first returns a JSON
  verdict (attack / benign / uncertain, MITRE ATT&CK tactics, evidence lines). Only "attack" runs the
  paper's prompts, copied verbatim from [AUTHOR CODE] `llm_prompt.py`: extract IOCs → drop IOCs not in the
  document (repo hallucination filter) → per-subgraph report; with ≥ 2 attack subgraphs, IOCs per APT
  stage → comprehensive report (Alg. 2 l.3–15).
  - Window verdict: attack if any subgraph is attack; uncertain if any is uncertain or failed; else
    false_positive. Triage evidence lines must appear verbatim in the document; if none do, the verdict
    becomes uncertain.
  - Serialization follows [AUTHOR CODE] `prepare_document` (subject ACTION the type : object, per minute,
    "(N times)" = distinct seconds). [OURS] real UTC times (the repo fakes a base date for SimulatedW10);
    full paths, not basenames; NET attrs decoded from SilkETW's signed integers
    (`-1,707,025,385:-17663` = `23.220.64.154:443`; display only, node IDs unchanged). The live UI shows the
    same decoded value with the raw one beside it (`engine.display_attrs`); saved window files stay raw.
  - No vector store or embeddings: a window's document fits in the prompt. Documents above 400 lines are
    cut, keeping lines that touch anomalous nodes first (count recorded).
  - Left out: Alg. 2 l.16–25 (an LLM judge picks top IOCs, their context is pulled from the graph DB, the
    report is enriched).
  - Provider is pluggable (`--llm-provider anthropic|openai|ollama|deepseek|mock`). [PAPER] used GPT-4o-mini
    (temperature 0) and LLaMA3-8B. The Anthropic backend sends no temperature (current models reject it),
    so its output is not deterministic. `mock` is a keyword rule for UI tests, not an LLM.
  - Provider choice (user, 2026-10-22): **any OpenAI-compatible endpoint** — `--llm-provider openai
    --llm-base-url <url>/v1 [--llm-model NAME] [--llm-api-key KEY]`. The key falls back to
    `$OPENAI_API_KEY`; a keyless endpoint used with a custom `--llm-base-url` gets a harmless dummy key so
    the SDK is satisfied ([OURS] `_resolve_openai_key`). Same flags on `live_server.py`, `rejudge.sh` and
    `alg2.py`. This covers self-hosted (vLLM / LM Studio / Ollama) and hosted (OpenRouter / Groq) servers.
  - First live run (2026-10-25, `ocr-live` with DeepSeek `deepseek-flash`, thinking off,
    `live-20261025T073042Z`): the one red window (a benign msedge launch, sg 33.87) → `false_positive`
    (confidence 0.9, 1 call, 3.1 s, 8/8 evidence lines found verbatim in the document): correct. A laptop
    replay of the session gave the same verdict (0.9, then 0.95 on a second replay: not bit-reproducible).
    The attack path (triage `attack` → IOCs → report) has **not** run live: the GNN produced no attack
    subgraph (D8 correction). One window = one data point.
  - Model choice (user, 2026-10-25): [OURS] DeepSeek API `deepseek-flash` (= DeepSeek-V4.1-Flash-1017,
    `--llm-provider deepseek`, OpenAI-compatible endpoint), not the paper's GPT-4o-mini / LLaMA3-8B. (Cloudflare
    Workers AI was tried first and dropped: the account's Free plan refuses this model.) By default DeepSeek
    thinks (effort high), and in thinking mode temperature has no effect, so verdicts and reports are not
    reproducible between runs. `--llm-reasoning-effort none` turns thinking off; temperature 0 then applies, but
    seed support is undocumented. No output cap is sent (DeepSeek's default is undocumented); a cut answer
    (`finish_reason=length`) prints a warning. The key comes from `NT2102_DEEPSEEK_API_KEY` or a prompt saved to
    `~/.config/nt2102/credentials.json` (mode 600).
- Why: the GNN alone cannot tell unseen-but-benign activity (the 11:25 `updater` window) from an attack;
  the demo needs a readable report for a real attack.
- Impact: the GNN alert is never removed. An LLM `false_positive` shows as a struck-through badge on a
  still-red window. Each red window costs 1 call (benign) or 3 calls (attack) per subgraph, plus 9 when
  ≥ 2 subgraphs are attacks. It runs on its own thread, so scoring is not delayed.
- Daily budget (user, 2026-11-02, [OURS]): at most `--llm-max-per-day` (default 10) red windows investigated
  per local day, to cap token spend on a 24/7 server. Windows over the budget keep their red GNN alert and get
  `skipped` instead of a verdict. Impact: on a busy day, later alerts have no LLM report; it limits nothing
  about the GNN detection. Budget counter in `sessions/llm_budget.json`.
- Limits: no claim that the LLM separates attacks from false positives until it is run on labelled
  windows (D6); one benign window is one data point, not a rate. An LLM verdict is not ground truth and is
  not fed back into training: log text (paths, process names) is attacker-controlled, so prompt injection
  is possible, and [PAPER] §3 assumes training logs free of attack traces. No claim about report quality
  vs the paper's Table 5.
- **Lab-artifact leak into the LLM evidence (2026-11-06, observed live, [OURS]):** on the exp08 live run
  the C2 window (00:50Z) alerted RED correctly (sg 37.62, the `10.21.4.30:8080` beacon flagged), but the
  LLM returned `uncertain` (0.4) not `attack`. Its rationale read the literal filename
  `c:\lab-labels\attack-labels.jsonl` (our self-labelling ground-truth file, D6) and inferred *"lab-like
  path … may be a test harness … rather than real adversary activity."* The anomaly detector is unaffected
  (SG-Det keys on path/`ip:port` **hashes** + features, never the readable string, so the alert stands);
  only the Alg. 2 overlay, which reads human-readable docs, is fooled, and only by a lab-only file.
  **Fixed (2026-11-06):** `lab_filters.is_lab_artifact_path` (matches `c:\lab-labels\*`, kept outside the converter so its sha stays) +
  `lab_artifact_node_ids` / `drop_lab_artifact_nodes` drop the ground-truth FILE node + its incident edges
  from both the training build (`build_experiment.py`) and live scoring (`engine._score`), so it never
  reaches Alg. 1 or the serialized LLM document. This is **not** reused from the watchdog filter
  (`is_own_tooling_path`): that drops the *process* that touched the file, which here is the attacker's RCE
  powershell — so lab-labels is a FILE-node drop that keeps the writing process. No-op on idle baselines
  (the file exists only during an attack). Covered by `lab_filters.py --selftest`.

## D10. Own Python ETW collector (`src/etw-python`, `etwcollect`) instead of SilkETW + Alloy
- What: [PAPER] the paper consumes existing audit datasets and names no collector. Until 2026-10-26 we used
  SilkETW/SilkService v0.8 (Mandiant, archived) writing JSON lines to files, shipped to Loki by Grafana
  Alloy. [OURS] (user decision 2026-10-26) one Python process, stdlib + ctypes only, run as the SYSTEM
  task `etwlab-collector`:
  - one real-time ETW session with the same 3 providers and keywords (Kernel-Process 0x50, Kernel-File
    0x380, Kernel-Network 0x30); payload decoded via TDH schemas cached per event type;
  - SilkETW's JSON line shape (top-level keys, `EventName` = task or task/opcode, payload in
    `XmlEventData` under manifest names), but plain values: ints without thousands separators, IPv4 dotted,
    ports in host order, UTC timestamps; no `FormattedMessage` and no TraceEvent header copies (`MSec`,
    `PName`, `TID`, header `PID`) inside `XmlEventData`;
  - exclusion **before** formatting, counted: events of its own PID; Read/Write on file objects whose
    Create named a path under the raw output root (any process, incl. System lazy writes); knet events
    whose payload PID is excluded; Read/Write on ETW's own real-time spill file for the session (System writes it);
  - one batched write per provider per second instead of one file open per event;
  - it pushes its own files to Loki (saved offsets, retry), with the labels/metadata/timestamps Alloy used,
    plus the dos-map line and the 07 workload ledger. SilkService and Alloy were removed from `ws-etw` the
    same day, after the checks below (`win_collector` uninstalls them).
  - Verified on `ws-etw` (2026-10-26):
    - parity: SilkService and etwcollect side by side, 20 s window: kproc 620/620, kfile 88,612/88,612 (the 76
      SilkETW-only lines are our own process), knet 26/26 events matched, 0 field mismatches after undoing
      SilkETW's formatting (`tools/parity.py`); our timestamps are 0.13–0.18 ms earlier than TraceEvent's;
    - 32-min live run with generated activity (~1,300 kfile events/s): kfile lag within ±4 s throughout
      (negative values = ETW timestamps a few seconds ahead of the system clock), `EventsLost` 0, collector CPU
      ~9–11 % of one core; Loki stopped for 2 min mid-run: 69 MB backlog, shipped within ~7 min of the
      restart; Loki per-provider counts = file line counts exactly (2,629,621 lines); the live engine got
      ~374k Read edges per 5-min window (SilkService runs: 0).
- Why: SilkService's per-event writes feed back into Kernel-File faster than it can drain them, and it has
  no exclude filter (keep-only, one value) and no batching; live
  windows had no file events (D8 correction). Own code is also fully explainable.
- Impact: raw lines differ in formatting from earlier captures, and process names of short-lived
  processes are real instead of `N/A`, so earlier experiments (exp01–exp03) are not comparable; a new
  benign capture + retrain (exp04) is required. The converter output contract is unchanged. Excluded events
  are gone from the raw data (counts in `collector-status.json`); exclusion is by PID and by our own file
  paths, not by name, so a binary named like the collector is still recorded. Anything written under the raw
  output root (`C:\lab-out\etw\raw`) by any process is dropped: an attacker hiding files there is invisible.
  Fields the manifest marks as hex (IOSize, ByteOffset, CreateOptions) are written as `0x..`. Live: a Loki
  outage longer than the engine's idle fallback makes the backlog arrive after its windows closed (dropped as
  late, D8); the files and Loki still hold every line.
- Limits: collector correctness is shown by the parity check against SilkService (one 20 s window) and the
  lag/lossless checks on one 32-min run with generated (not workload) activity; no claim that it captures
  every kernel event (`EventsLost` is reported, not prevented).

## D11. Linux endpoint `ubuntu-etw`: auditd + own Python shipper (`src/audit-python`, `auditship`), raw logs only
- RETIRED (user, 2026-11-06): the VM is replaced by a second Windows VM `ws-etw-1` (D12). The code stays in the repo
  unused; the text below is the record of what it was.
- What: [PAPER] the paper evaluates Linux hosts only through existing datasets (DARPA TC3 / OpTC
  provenance records); it runs no collector. [OURS] (user decision 2026-11-02) a second endpoint VM on the
  Proxmox lab, `ubuntu-etw` 10.21.3.11, records syscalls with **auditd** and ships every record unchanged to the
  trainer's Loki as `{job="auditd", host="ubuntu-etw"}` (structured metadata `type`, `key`; entry time = the
  record's own `audit(<epoch>.<ms>:<serial>)` time). Roles `linux_auditd` + `linux_auditship`, playbook
  `src/ansible/target.yml`. Rules (`50-nt2102.rules`): the shipper's uid is excluded first (`-a never`), then
  `execve/execveat` (`-k exec`), 64-bit `connect/accept/accept4` (`-k net`), and recursive write/attribute
  watches (`-k file`) on `/etc /home /root /tmp /var/tmp /usr/local/bin /opt`. No global `open()` rule, so file
  *reads* outside execs are not seen.
  - Shipper: own stdlib Python (like D10, not Alloy), reusing `etwcollect.loki.LokiClient`; tails `audit.log`
    from a saved `{inode, offset}`, finishes the rotated file before the new one, advances only after Loki's 204.
  - Verified on `ubuntu-etw` (2026-11-02): `curl example.com` and a `/tmp` write appeared in Loki within seconds
    with `key=exec` / `net` / `file`; 0 records with the shipper's uid (no feedback loop); port 3100 blocked
    for 40 s with 20 marked execs: backlog ~107 KB, drained on unblock, 20/20 markers in Loki, no duplicates;
    `target.yml` and `ubuntu.yml --tags loki` re-run with changed=0.
- Why: a Linux event stream next to the Windows one, from the same lab and the same Loki. The shipper is our own
  for the same reason as D10 (every line explainable, one push contract), and its uid is excluded so its own
  connects and file writes don't feed back (the SilkService loop, D8/D10).
- Impact: Loki gets a second job; the training and live paths select `job="silketw"` and are unchanged.
- Claims limited: **none of the detection results apply to Linux.** There is no auditd → NODLINK converter, no
  features and no model for these records; they are not scored. Coverage is limited to the rules above (exec,
  64-bit connects, writes under the watched dirs), not a full provenance graph.

## D12. Two Windows hosts: one model per host, each scored live
- What: [PAPER] evaluates each host separately. [OURS] (user, 2026-11-07, replacing the pooled plan of 2026-11-06) `ws-etw-0` and
  `ws-etw-1` push to one Loki and each has its own model: `lab promote <model> --host <vm>`, `lab hosts <vm> ...`. One
  `Engine` + `LokiSource` runs per scored VM, with its own model, scaler and clock; history and LLM budget are shared.
- Why: matches the paper, keeps `ws-etw-0` on exp08 with no retrain, and avoids one baseline for two different machines
  (`ws-etw-1` has no XAMPP/MariaDB, so a pooled baseline would be wider and [INFERENCE] less sensitive on `ws-etw-0`).
- Still in the code: `lab --host` twice builds a pooled experiment with `merge_experiments()`, each window one host's graph
  (one build would merge both hosts' FILE/NET IDs into one graph). Not used live.
- Expected impact: a VM's windows are unchanged by adding another (selftest: identical to a plain engine). Each VM needs its own
  clean idle baseline. One-VM mode is `lab hosts ws-etw-0`: the same code with one engine and no correlation.
- `ws-etw-1` model, history: `exp11-ws1` (2026-11-11, 55 h idle to 11-09 10:10Z) failed the gate: NET stopped early on all 3 seeds.
  The cause (found 2026-11-12, verified in `train_scores.csv` + node attrs): the NetBIOS broadcast `10.21.3.255:138` (kernel `System`)
  first appears on `ws-etw-1` at 11-09 09:19Z, so it sat only in the validation tail and never in training; 5 of the 6 validation NET
  nodes over τ were that broadcast. `ws-etw-0` has it in 231 training windows. It was **not** one-off peers: `ws-etw-0` meets as many
  new peers (18 % of validation NET nodes) and flags none, because features are behaviour, not addresses. `exp14-ws1` (tol 0.06)
  passed by tolerating those nodes; never deployed.
- `ws-etw-1` model, live since 2026-11-12 ~14:10Z: **`exp15-ws1`**, 11-06 20:30Z → 11-12 12:15Z (teammate setup + RDP included, attack
  runs excluded, D2), tol 0.01 like `ws-etw-0`. Gate (seed 360 = the deployed model): best_ep 99 on FILE / NET / PROCESS, val_fpr
  0.0004 / 0.0000 / 0.0020. Deployed in two-VM mode (`lab hosts ws-etw-0 ws-etw-1`) by user decision, who allowed deployment even if the
  gate failed.
- `ws-etw-0` model, history: `exp10` (≈63 h idle, 11-06 19:40Z → 11-09 10:25Z) was trained **16 min before** the DC promotion
  (11-09 10:41:41Z), so it was blind to all AD services — the live red=0 under it was steady-state luck, not a covered baseline. Retired
  from live use 2026-11-12. **`exp16-ws0`** live since 2026-11-12 ~16:05Z: DC-era benign 11-09 16:49Z → 11-12 15:08Z (DC steady-state +
  admin sessions, D2), tol 0.01 like `ws-etw-1`. Gate (seed 360 = deployed): best_ep 99 on FILE / NET / PROCESS, val_fpr
  0.0002 / 0.0000 / 0.0023; the NET gate — the DC risk — is the strongest (2,130 val nodes) now that the DC's NET is in training.
  Deployed two-VM by user decision (deploy allowed even on gate failure). First check: 0 red on both hosts.
- Claims limited: green windows on either host show its baseline matches its current use, not that it detects attacks: no labelled
  attack has been replayed or run against `ws-etw-1` or the DC `ws-etw-0` after their DC-era/current-state retrain. Human/admin sessions
  on both are now learned as normal (D2), so the old interactive-session false alarms no longer fire — and neither would an attacker who
  looks like the admin. No cross-host attack has crossed the two yet (D13).

## D13. Cross-host correlation (`src/live/correlate.py`)
- What: [PAPER] §7: "Detecting lateral movement was beyond the scope of this work, as OCR-APT does not process network traffic
  logs", left to future work. [OURS] (user, 2026-11-07) a stage after per-host SG-Det. Plain code reads raw knet lines, keeps
  unicast flows between monitored hosts that are not in a known-traffic list (`lab flows`), and joins them with each host's
  windows on the shared 5-min grid, lookback 3 windows: `link`, `suspected` (one end red at or before the flow), `confirmed`
  (both ends red). On suspected and confirmed, the LLM gets both hosts' subgraphs and the link as one document: an [OURS]
  multi-host triage prompt, then the paper's IOC and report prompts.
- Why: each model only sees its own host's graph, so no model can treat a connection as two ends of one thing. The link is found
  by code because the LLM runs only on red windows, within a budget, and log text is attacker-controlled (D9).
- Details: raw lines, because the converter keeps only `daddr:dport`, so a receiving host's NET node is the peer's ephemeral
  port. Reading them outside the converter keeps its sha256, so exp08 stays valid. Addresses are compared with the host's own IP:
  verified in Loki, TCP has `saddr` = this host both ways, UDP receive has `daddr` = this host or broadcast. The lower port is
  taken as the server [INFERENCE]; with both ports dynamic, direction is unknown and the key uses host order.
- Impact: none on any window or alert: the result is an overlay. The host with no kept subgraph still gives its best subgraph to
  the LLM document, since a short hop can stay under SG-Det's cut-off. Adds LLM calls to the same daily budget.
- Limits: validated on synthetic flows only. No attack crossing hosts has been run, so **no claim that real cross-host movement
  is detected**. The planned first real crossing (user, 2026-11-12) is **react2shell (`ws-etw-1`) → DC (`ws-etw-0`)** over SMB/WinRM;
  until it runs and is labelled, this no-claim stands, and a `lab flows` baseline must be recorded first (benign AD replication/DNS/SMB
  between the two VMs is new to the baseline otherwise). Needs a clean baseline, or every flow is new. A `suspected` needs a red window,
  so a hop from a host that stayed green is only a `link`. Only flows between monitored hosts are seen. Windows on different hosts join by
  clock: skew between VMs was not measured.

## D14. Process command lines from Sysmon Event ID 1 (`src/pipeline/cmdlines.py`, role `win_sysmon`)
- What: [PAPER] §2 lists command-line arguments among a provenance graph's contextual node attributes; §5.1.1 keeps such
  attributes **out of the OCRGCN input** (spurious correlations) but "retained for the investigation phase". Our ETW
  `Microsoft-Windows-Kernel-Process` ProcessStart event has **no CommandLine field** (only ProcessID, ParentProcessID, SessionID,
  Flags, ImageName), so the UI and the LLM document showed only `powershell.exe` / `cmd.exe`. [OURS] (user, 2026-11-13) Sysmon is
  installed with an **Event ID 1 (ProcessCreate) only** config and read by `etwcollect` as a 4th, optional ETW provider (stem / Loki
  `provider` = `sysmon`). A join module maps each PROCESS node `p<pid>_<t>` to the Sysmon record with the same PID and the latest
  start ≤ t + 2 s. The live engine shows it under the process in the UI tables, the graph tooltip and the alert card, writes
  `node_cmdlines.csv` per window, and `alg2.serialize` puts one line per process (`process <image> (pid N) command line: … |
  decoded: … | parent command line: …`) **before** the [AUTHOR CODE]-format event lines, so the LLM can cite it as evidence.
- Why Sysmon, not reading the live process: ETW delivers events up to ~1 s late (FlushTimer 1), so short-lived children
  (whoami, mysql, net) are gone before the collector could open them. Sysmon captures the command line at creation (checked on
  `ws-etw-0` 2026-11-13: `whoami /all`, 22 ms after its `cmd` parent, recorded with its arguments).
- Investigation-only: the converter (`etw2nodlink.py`, sha256 unchanged) counts Sysmon lines as unmapped, and the live engine does
  not even pass them to it. Edges, features, `scores.csv` and alerts do not change; **no retrain**. Training pulls (`loki_pull`
  range mode) still default to kproc/kfile/knet.
- Display copy: PowerShell `-enc` payloads are decoded (also inside `Start-Process -ArgumentList '-nop','-enc','…'`); best-effort
  secret redaction (`-Password x`, `password=x`, `/pass:x`, `ConvertTo-SecureString "x"`); each field cut to 400 chars. The raw
  Sysmon line in Loki and `raw/sysmon.json` is not redacted.
- Lab vocabulary (D9 rule, new channel): the RCE cradle's decoded payload carried `$env:LAB_ATTACKER`, `attack-lib.ps1` and
  `killchain.ps1`. These lab names would push the LLM to "attack" for a reason a real intrusion lacks, so the stage now serves
  `/u.ps1` + `/s.ps1` and uses `U_H`/`U_P`/`U_D` (`launch_exploit.py` selftest asserts no lab words in the decoded cradle).
- Impact: the LLM sees more evidence, so **LLM verdicts before and after D14 are not comparable**. A third-party driver
  (Sysmon64) now runs on the VMs; extra events = one per process start.
- Status: deployed on **both hosts** (`ws-etw-0` 2026-11-13, `ws-etw-1` 2026-11-13); end-to-end verified on `ws-etw-0` (DVWA attack
  window showed decoded command lines in the UI + LLM, no lab words, `converter_check` match).
- Limits: processes started before Sysmon/the capture (`p<pid>_pre_<run>`) never match.
  The match assumes no lost Sysmon events; a lost one would attach the PID's previous owner's command line (the `delta_ms` column
  in `node_cmdlines.csv` shows how far apart the two stamps are). The redaction is pattern-based, not a guarantee: command lines
  still go to the external LLM provider.
