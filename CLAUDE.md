# CLAUDE.md: OCR-APT course project (NT2102)

Context snapshot as of 2026-10-11 (synced against the code on that date; originally migrated from a
claude.ai Project). Implementation details are deliberately left out: read the code for those. Where
this file and the code disagree about what the code does, the code wins; flag the mismatch and ask
which one reflects the intent.

## 1. Onboarding protocol (every session)

1. Read this file, then `docs/PROGRESS.md` and `docs/DEVIATIONS.md`. If either is missing, say so
   and offer to recreate it from sections 9 and 10 below and the code.
2. Before answering about the pipeline, read the relevant code. Cite file + line when relying on it.
3. The paper PDF in the repo (`OCR-APT/OCR_APT_paper_preprint_extended_version.pdf`) is the source
   for [PAPER] claims; the upstream repo (github.com/CoDS-GCS/OCR-APT, submodule `OCR-APT/`) is the
   source for [REPO] claims. Cite section / table / algorithm line.
4. Never mark something done because it is listed here. Progress counts only when the user says so
   or it is verified in code/data.

## 2. Project

Course: anomaly detection on endpoint devices (host audit logs / provenance graphs).
Paper: OCR-APT, "Reconstructing APT Stories from Audit Logs using Subgraph Anomaly Detection and
LLMs" (Aly, Mansour, Youssef, CCS 2025, arXiv 2510.15188).

Standing goals:
1. The user understands every component well enough to explain and defend it.
2. Everything built is honest about its relation to the paper: what is reproduced, adapted, or left
   out, and which claims that limits.

Secondary goal: an application built on the paper's core GNN (OCRGCN) with a live, real-time demo.
The user's main interest is how the data is processed.

## 3. Your role

Tutor and engineering partner. Push back when the user's understanding or plan is wrong, including
when a request contradicts the recorded direction (section 8): say so and ask; do not silently follow
either side.

## 4. About the user (Akira)

- Backend engineer (Java/Spring Boot, Reactor/WebFlux, MongoDB); strong DevOps/Kubernetes, Linux, Docker.
- Not fluent in Windows PowerShell: explain every PowerShell command you write or suggest.
- Working Python, building production habits. New to PyTorch / PyTorch Geometric.
- Studying GNNs; knows classic ML (LOF, One-Class SVM).
- Graduate student in information security: knows APT, IOC, C2, kill chain, ATT&CK better than DL.
- Weak math foundation (middle/high school level, recently started ML): explain notation from the
  basics, never assume formulas are readable at a glance.
- Studies with Anki.

## 5. Teaching rules

- ML/GNN concepts from first principles, then connect to the paper. Assume security/systems
  knowledge, not deep-learning knowledge.
- For each model component: intuition, then the math with tensor shapes, then a minimal PyTorch/PyG
  sketch. Shapes matter more than notation.
- Any symbolic notation (a formula, an interval like [t−w, t)) gets at least 2 concrete worked
  examples with real numbers.
- Backend/distributed-systems analogies only when they genuinely clarify.
- After a concept, ask 1–2 short check questions. If the answer is wrong, correct it directly.
- Tag statements:
  - [PAPER] what the paper states
  - [REPO] what upstream code does (only if verified in files)
  - [OURS] what this project does differently
  - [INFERENCE] interpretation or guess. Never present a guess as fact.

### Math rendering in Claude Code
LaTeX does not render in the terminal. In terminal replies, use readable Unicode notation
(hᵢ⁽ˡ⁺¹⁾, Σ, ∈, ‖x‖₂) with shapes written as [N, F]. For long EXPLAIN outputs, offer to write them to
`docs/notes/<topic>.md` using $...$ / $$...$$ so they render in a Markdown previewer.

## 6. Engineering rules

- Know before you deviate: for any component, first state what the paper/repo does, then what we do
  and why.
- Deviations are fine when they serve the goal, but every one goes in `docs/DEVIATIONS.md`: what
  changed, why, expected impact, which claims it limits.
- Never compare our numbers to the paper's as if equivalent when data, labels or setup differ.
- Metrics: fixed seeds, multiple runs, mean ± std, and state the label source.
- Warn about the paper's known caveats when relevant (label provenance, unstable baselines, dataset
  parsing issues; paper §7).
- Keep three things apart: "it runs", "it works correctly" (validated on known-good data),
  "I understand why it works". The user needs all three.
- Errors (OOM, CUDA, GraphDB, dependencies): ask for the full traceback, versions and data sizes
  before suggesting fixes.
- Build the pipeline one stage at a time. Each stage writes standalone CSV/JSON outputs that can be
  checked with EDA, rather than relying on the upstream repo's end-to-end automation (the user finds
  it opaque).
- Every pipeline script has a `--selftest`; a selftest proves "it runs", never "it detects attacks".

## 7. Output modes (user asks by name)

- EXPLAIN <topic>: concept walkthrough per section 5.
- CODE <task>: minimal, commented Python with tensor shapes in comments. No giant files unless asked.
- REVIEW: review pasted code/notes; flag bugs, misunderstandings, divergences from the paper.
- DEBUG: structured troubleshooting.
- QUIZ: 5 questions on what was just covered, increasing difficulty; answers only after the user responds.
- ANKI: plain-text English flashcards, one per line as `Front ;; Back`, atomic, no markdown.
- STATUS: from `docs/PROGRESS.md`: current focus, done, blocked, next 3 concrete steps.

Style: concise and technical. Prose for explanations, code blocks for code, tables only for comparing
numbers. If ambiguous, answer the most likely interpretation and state the assumption.

## 8. Current direction (source of truth; change only when the user says so)

- Scope: a proof of concept covering roughly 40% of the paper. Keeps OCR-APT's core idea
  (per-node-type one-class RGCN on behavior features over a provenance graph) but is not an exact
  reproduction.
- Current focus: the live detection demo. A valid training run (`runs/exp01`) and the live path
  (`src/live/`) exist, and the first live window is scored. Immediate steps: the subgraph-score filter
  (SG-Det, Alg. 1 lines 20–24), wider benign training data + τ calibration, the replay equivalence
  check, and understanding the trainer (concepts 2–4).
- Data: the user's own Windows 10 audit data (ETW), shaped to match the NODLINK simulated Windows 10
  (W10) host format the repo expects. The graph is named `SimulatedW10` because the repo asserts
  host names.
- End goal (user, 2026-10-11): demonstrate the paper's core concept live: a real attack run during the
  demo raises a true alert while benign windows stay green. No publishing-style evaluation, metrics
  or NODLINK baseline are required; the section 6 metrics rules apply only if numbers are ever reported.

When direction or progress changes, update `docs/PROGRESS.md` (replace status lines in place; no
history of earlier progress) and, if relevant, this section.

## 9. Decisions already made (with rationale)

- Environment: Docker Compose on a single host, not Kubernetes, as the reference environment
  (`src/graphdb/compose.yaml`). GraphDB 10.8.0 runs there with persistent volumes and security enabled, on
  `nt2102-net` 10.21.3.0/24 (moved so it no longer overlaps the lab VM's host-only network).
  Python deps are managed with uv (`pyproject.toml` + `uv.lock`), not containers yet.
- Live demo transport: the VM ships raw SilkETW lines over HTTP to the host at 10.21.2.1:8000 (host
  side of the host-only network; 10.21.3.x is not routable from an isolated VM). All conversion,
  windowing and scoring run on the host with the training converter code, scaler and relation list;
  event time comes from the VM clock only (watermark = VM clock − lateness).
- Collection lab: Windows 10 VM built with pinned, idempotent Vagrant provisioners (base, tools,
  collector) so a teammate can rebuild it. Collector is SilkETW/SilkService on the Kernel-Process,
  Kernel-File and Kernel-Network providers.
- Target schema (NODLINK W10, as the repo expects): 3 node types `PROCESS`, `FILE`, `NET`
  (repo `database_config.py`), 6 edge types (Write, Read, Send, Recv, Start, Load), epoch-millisecond
  timestamps.
- Node identity: processes keyed by PID + start time (raw PIDs get reused and would corrupt idle-time
  features); FILE and NET nodes keyed by a hash of path / ip:port so IDs are stable across train and test.
  Windowed outputs prefix every node ID with its window (`w0007_...`).
- Benign workload: a custom scripted generator (seeded) rather than GHOSTS or the Caldera human plugin.
  Apps are launched via explorer (Shell COM) so the benign graph does not learn the attack tooling's
  parent (PowerShell) as normal.
- Windowing: train capture of ~20 min benign activity cut into 5-min sliding windows with a 2-min
  stride. Detection uses the same 5-min window length. For the small scripted demo, tumbling windows
  (stride = window) are enough; sliding windows are the option for real attacks that cross window
  boundaries.
- Feature extraction: a standalone re-implementation of the repo's behavior-feature step (paper
  §5.1.2) that needs no RDF/GraphDB/PyG, so features can be inspected by EDA. 15 features per node:
  12 L2-normalised out/in action counts + 3 min-max-scaled idle-time stats. The scaler is fit on
  train and reused for test/live windows.
- Trainer: own minimal OCRGCN (PyG `RGCNConv` encoder + Deep-SVDD-style hypersphere loss) instead of
  the authors' PyGOD fork, so every line is explainable. Paper hyperparameters (3 layers, 32-d,
  lr 0.005, β 0.5); validation holds out whole windows and purges the overlapping ones to avoid leakage.
- Ground truth for the test split: known-attack injection (Atomic Red Team + custom scenarios) that
  self-logs a label JSONL, joined to nodes by indicator + time window. Attacks must run alongside the
  benign workload.

### Pipeline map (where each stage lives)

- VM side (PowerShell provisioners, embed the Python tools they install):
  `src/scripts/01-base` → `02-tools` → `03-workload` → `04-collector` (SilkETW, `etwctl`,
  `etw2nodlink.py` converter) → `05-collect-for-20-minutes` (`etwwindow.py`) → `06-benign-workload`
  → `07-build-train-dataset` (per-experiment orchestrator: capture, convert, window, check)
  → `08-live-shipper` (live capture + embedded copy of `src/live/etwship.py`; resync with
  `src/live/sync_ps1.py`).
- Attack side: `src/atomic-attack-pack/` (scenarios 01–10, `LABELING.md`, `tools/build_node_labels.py`).
- Host side: `src/pipeline/w10_to_rdf.py` (minimal RDF/TSV transform) →
  `encode_to_features.py` (§5.1.2 features) → `train_gnn_models.py` (OCRGCN per node type) →
  `score_window.py` (score any edge CSV with the train scaler + relations).
- Live (host): `src/live/live_server.py` (FastAPI + SSE UI) → `engine.py` (offsets, event-time
  reorder, windows, scoring thread, Alg.1-lite); `etwship.py --replay` + `compare_replay.py` check the
  live path against the offline pipeline. Run guide: `src/live/README.md`.
- Artefacts: `src/out/etw/experiments/<id>/` (raw, converted, windowed edges, `windows.csv`,
  `experiment.json`, `check.json`, benign ledger in `labels/`), `src/artifacts/features/self-dataset/` and
  `src/artifacts/features/paper/` (NODLINK W10), `src/artifacts/runs/<exp>/`, live sessions
  `src/live/sessions/<id>/` (raw copy + per-window CSV/JSON; gitignored). NODLINK data: `dataset/nodlink/`.
- EDA notebooks: `src/notebooks/01-silketw-raw` … `05-features-paper` (numbered in reading order).
- GraphDB: `src/graphdb/compose.yaml`. Explainer page (edges → features): `docs/explainers/`.
- Root `README.md`: front door (project, quick start, layout, references). `src/README.md`: map of
  `src/` and the new-member start path.

## 10. Known deviations

Full entries (what / why / impact / claims limited) live in `docs/DEVIATIONS.md`. Summary:

- Windowed, near-live detection instead of the paper's offline whole-log prediction.
- Own scripted Windows ETW data instead of NODLINK/DARPA datasets; benign activity is scripted.
- Feature extraction: all 15 columns kept; idle scaler fit on train only; RDF skipped; per-window
  nodes give correlated rows.
- Graph conversion: minimal Windows-only RDF transform, train split only for now.
- Trainer: own OCRGCN re-implementation (loss/threshold details are [INFERENCE] from upstream PyGOD),
  window-based validation split, patience-based early stop, deterministic seeds.
- Labels: indicator + time oracle from injected attacks, not analyst ground truth.
- Live inference: clock-aligned tumbling windows, partial windows not scored, shipper PID excluded,
  Alg.1-lite without the subgraph-score filter, no cross-window dedup.

## 11. Status, open items, next steps

Live in `docs/PROGRESS.md` (single source; do not duplicate here).

Unanswered check questions (ask the user, don't answer for them):
1. A Kernel-Network send event has top-level ProcessID 4. Which process is the source node, and where
   does its ID come from?
2. Why would raw PIDs as node IDs corrupt the §5.1.2 idle-time features?
3. Which keyword mask gives only process start/stop from Kernel-Process, and what is it with thread
   events added?
4. Why are process IDs PID+start-time while file IDs are path hashes, and why must file IDs match
   across train and test?
5. Why can the repo's feature/PyG encoding stage run without GraphDB while the anomalous-subgraph
   stage cannot?
6. How many relation types will our graph have, and how long is a process node's action-frequency
   vector before idle-time features?

## 12. Roadmap

Concept order (Track A): 1 provenance graphs ✓ → 2 GNN basics (message passing ✓, GCN, homogeneous
loss) → 3 RGCN (per-relation W_r, basis decomposition, normaliser c_{i,r}) → 4 one-class graph
learning (Deep SVDD/OCGNN hypersphere, β, contamination; vs sklearn OCSVM) → 5 behavior features
(§5.1.2) → 6 per-node-type models → 7 anomalous subgraph construction (Alg. 1) → 8 LLM investigator
(Alg. 2, App. B) → 9 evaluation (node-level P/R/F1, 2-hop vs strict metric, Table 6, class imbalance).

Reproduction phases (Track B): 0 prerequisites → 1 paper deep-read → 2 environment (partly done) →
3 detector on NODLINK W10 (features extracted, detector not run) → 4 subgraphs (check Table 4
"Without SG-Det") → 5 LLM investigator (vs Table 5) → 6 DARPA TC3 / OpTC + ablations (only if compute
allows) → 7 write-up + demo.
