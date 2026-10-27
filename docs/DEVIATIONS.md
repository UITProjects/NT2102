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

## D2. Own scripted Windows ETW data
- What: [PAPER] NODLINK / DARPA TC3 / OpTC. [OURS] ETW Kernel-Process/File/Network on a Windows VM (SilkETW
  until 2026-10-26, then our own collector, D10),
  converted to the NODLINK SimulatedW10 schema; benign activity from a seeded scripted workload
  (`src/scripts/06-benign-workload.ps1`), plus background OS activity.
- Why: own end-to-end pipeline for the live demo; dataset small enough to inspect.
- Impact: benign behaviour is narrower than human use → likely optimistic false-positive rates;
  converter drops/unresolves some events (see converter `_summary.json`).
- Limits: our numbers are not comparable with the paper's tables.

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
  page (T1190/T1059.001), then the RCE shell runs Discovery (T1033/T1082/T1057/T1016) → ingress transfer
  (T1105) → persistence (T1053.005). The external origin means the attack subtree is rooted at the vuln
  service (`httpd.exe → cmd.exe → powershell → …`), which is itself the anomalous signal; indicators are
  the tool image names, the downloaded file, the `UpdaterSync` task, and `<attacker>:8080`. DVWA's
  command-injection page (`/vulnerabilities/exec/`, security=low) is a real, documented and intentional
  OS-command sink, so the exploited RCE is faithful. (Earlier: 10 Atomic/custom scenarios run on the VM.)
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
  - [OURS] level scale (user decision, 2026-10-13): ≤ 1 Negligible, < 30 Minor, 30–100 Moderate,
    ≤ 1000 Significant, else Critical. [AUTHOR CODE] l.394 has Moderate > 10; other bounds unchanged.
    `--sg-moderate-from` (default 30; `10.0001` = [AUTHOR CODE] scale). A window alerts only if ≥ 1 subgraph
    is ≥ Moderate.
  - [OURS] no top-k fallback when nothing reaches Moderate ([AUTHOR CODE] l.433–461); live, it would make every
    window red.
  - [OURS] a partition still above max_e is kept whole (`oversize`), not sampled per edge type.
  - [OURS] optional numeric cut-off `--sg-min-score`, to be calibrated on benign sessions only.
  - [OURS] the anomalous set is capped at the 300 highest margins (UI guard).
- Why: live demo, with the same code path as offline so a replay of the training capture can be checked
  against the offline artefacts (`compare_replay.py`). Level scale: [PAPER] §5.2 says the subgraph set
  "can be adjusted based on the desired abnormality threshold". A 5-min window is far smaller than the
  paper's whole-log graphs, and the [AUTHOR CODE] cut-off (> 10) left the benign 11:25 `updater` window red.
  30 = that window's score (18) plus margin.
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
- Limits: shows near-live node scoring + Alg. 1 on our own data. No detection-quality claim until windows
  are labelled (D6); no claim about Alg. 1 precision; the cut-off (Moderate ≥ 30) is ours, set on one
  benign window, not the paper's.

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
- Limits: no claim that the LLM separates attacks from false positives until it is run on labelled
  windows (D6); one benign window is one data point, not a rate. An LLM verdict is not ground truth and is
  not fed back into training: log text (paths, process names) is attacker-controlled, so prompt injection
  is possible, and [PAPER] §3 assumes training logs free of attack traces. No claim about report quality
  vs the paper's Table 5.

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
  no exclude filter (keep-only, one value) and no batching (`docs/notes/silkservice-kfile-lag.md`); live
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
