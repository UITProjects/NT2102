# CLAUDE.md: OCR-APT course project (NT2102)

Context snapshot as of 2026-11-07. Implementation details are deliberately left out: read the code for those. Where this file and the
code disagree about what the code does, the code wins; flag the mismatch and ask which one reflects the intent.

## 1. Onboarding protocol (every session)

1. Read this file, then `docs/PROGRESS.md` and `docs/DEVIATIONS.md`. If either is missing, say so
   and offer to recreate it from sections 9 and 10 below and the code.
2. Before answering about the pipeline, read the relevant code. Cite file + line when relying on it.
3. The paper PDF in the repo (`OCR-APT/OCR_APT_paper_preprint_extended_version.pdf`) is the source
   for [PAPER] claims; the upstream repo (github.com/CoDS-GCS/OCR-APT, submodule `OCR-APT/`) is the
   source for [AUTHOR CODE] claims. Cite section / table / algorithm line.
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
- Python code explanations (user, 2026-10-20): before explaining what a line does, label the type of
  every variable and sub-expression (`nodes: pd.DataFrame`, `nodes["node_uuid"]: pd.Series[str]`,
  `attrs: dict[str, str]`, `x: torch.Tensor [N, 15]`), e.g. as a piece | type | meaning table or
  inline comments. Call out when `[]` or `=` is not plain indexing/assignment (DataFrame column
  write, boolean mask, dict lookup) and give the Java equivalent when it helps. Same in CODE mode
  comments and in explainer pages.
- Do not ask check questions unprompted (user, 2026-10-13). Quizzing happens only in QUIZ mode.
- Tag statements:
  - [PAPER] what the paper states
  - [AUTHOR CODE] what the authors' upstream code (github.com/CoDS-GCS/OCR-APT) does (only if
    verified in files). Never write [REPO]: it reads as this project's own repo.
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
- Errors (OOM, CUDA, dependencies): ask for the full traceback, versions and data sizes
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

- Scope: a proof of concept of the paper's core (no coverage percentage: user, 2026-10-18). Keeps OCR-APT's core idea (per-node-type
  one-class RGCN on behavior features over a provenance graph) but is not an exact reproduction.
- End goal (user, 2026-10-11): demonstrate the paper's core concept live: a real attack run during the demo raises a true alert while
  benign windows stay green. The attack is general, not APT-specific (user, 2026-10-13): exploit of a lab service → RCE → ATT&CK techniques;
  OCR-APT supplies the method. No publishing-style evaluation, metrics or NODLINK baseline are required; the section 6 metrics rules
  apply only if numbers are ever reported.
- Now live (since 2026-11-09 16:08Z): `ocr-live` on `ubuntu-trainer` scores `ws-etw-0` with **exp10** (~63 h idle, 2 RGCN layers) and the
  [OURS] NET alert rule (D8); exp08 before that. The end-goal demo worked on exp08: Kali's DVWA→RCE chain went red with an LLM `attack`
  verdict and idle stayed green around each run; exp10 keeps all 5 attack windows red offline, live Kali run still owed. It is not airtight: benign false alarms
  still happen and the alert can rest on a few nodes (PROGRESS, `docs/notes/subgraph-score-fragility.md`).
- Two hosts (user, 2026-11-07): one model per host, not pooled (D12). **Two-VM live since 2026-11-12 ~14:10Z** (user decision): `ws-etw-0`
  runs exp10, `ws-etw-1` runs **exp15-ws1** (gate passed at tol 0.01; `docs/notes/ws-etw-1-retrain-20261111.md` §11). `ws-etw-1` belongs to a
  teammate, who runs react2shell (a vulnerable Next.js RCE app) and a SYSTEM task `maintenance.ps1 → whoami` every minute on it; by user
  decision its baseline includes the teammate's setup and RDP sessions, only their exploit/enumeration runs excluded (D2). The trainer
  runs `main` via Ansible `--tags project` (hosts file written, correlation on). `lab hosts` switches one-VM / two-VM. A correlation stage
  links hosts by code and the LLM explains the full flow (D13); no `lab flows` baseline yet, no attack has crossed hosts. Older data says
  `ws-etw`: Loki lines from before the rename keep that label.
- **Deliverables (slides + demo) commit to ONE Windows VM** (`ws-etw-0`, exp08): the recorded demo and the deck show a single host.
  The second VM (`ws-etw-1`) and cross-host correlation are **nice-to-have**, shown only as future work unless there is time (user, 2026-11-08).
- The Slidev deck (`presentation/`) was redesigned to the user's structure on 2026-11-08 (committed), then **compacted to 13 slides,
  keywords only, appendix, results and limits slides removed; demo goes straight to thanks** (user, 2026-11-08): details live in the report (`latex/`) and Q&A in `note.txt`.
- The LaTeX report (`latex/contents.tex`) was **restructured to the deck's 5-part shape and content-aligned on 2026-11-08**
  (§1 Giới thiệu → §2 Ý tưởng cốt lõi → §3 Demo → §4 Giới hạn & thách thức → §5 Kết luận, detailed deviations in an Appendix):
  one Windows VM (`ws-etw-0`, exp08, 2 RGCN layers), benign = idle 3-day, Moderate from 10 (paper scale), exp08 results filled
  (R2 attack windows 70.17/54.42 Moderate, LLM `attack` 0.85/0.9; 288 windows / 7 alerts / 3 benign FP), D1–D13. Builds clean
  (`latexmk -pdf main.tex`, 21 pages). Only demo screenshots + LLM-provider name left as `\todoval`. Committed (PR #15).
- **Next action (user, 2026-11-12): demo lateral movement DVWA → react2shell** — a real cross-host attack (foothold on `ws-etw-0` → pivot
  to `ws-etw-1`'s react2shell RCE) so correlation (D13) fires on real flows, not synthetic. Prereqs: `lab flows` baseline first, a new
  network-delivered attack scenario in `src/atomic-attack-pack/`, and proof the exploit scores red on `ws-etw-1` (its `node` server is
  in-distribution; D6 risk). Also owed: 24 h two-VM soak. Details + full prereqs: PROGRESS top. Then concepts 2–4.
- SG-Det uses the **paper's level scale** (user, 2026-11-03): Moderate from 10, [PAPER] §7.2.1 (D8). `ocr-live` keeps a persistent, range-queryable
  window history (`src/live/history.py`).
- Attack target: DVWA on XAMPP on `ws-etw-0` (user, 2026-10-19), its OS-command-injection page (`/vulnerabilities/exec/`) as the RCE sink,
  on :3000, login admin/password, security low.
- Work happens on the Proxmox VMs, controlled over Ansible/SSH.
- Data: the user's own Windows audit data (ETW), shaped to match the NODLINK simulated Windows 10 (W10) host format the repo expects. The
  graph is named `SimulatedW10` because the repo asserts host names.

When direction or progress changes, update `docs/PROGRESS.md` (replace status lines in place; no
history of earlier progress) and, if relevant, this section.

## 9. Decisions already made (with rationale)

- Environment: Python deps are managed with uv (`pyproject.toml` + `uv.lock`), not containers. Docker is used only on the Ubuntu trainer,
  for Loki + Grafana and the Prometheus monitoring stack (installed by Ansible). No RDF / GraphDB (user, 2026-10-17): edges stay CSV and
  Alg. 1 runs in memory.
- Log transport: our collector (`src/etw-python`, `etwcollect`) pushes SilkETW-format JSON lines to Loki (Docker, host network) on the trainer,
  from its own run files with saved offsets. Labels: job (`silketw`, it names the line format), host, split, run, provider; event/pid/process
  as structured metadata. Training pulls from Loki by run or time range (`loki_pull.py`); the live server follows Loki (`--loki-url`). All
  conversion, windowing and scoring run on the trainer with the training converter code, scaler and relation list; the live clock is event
  time (newest event − lateness), with a host-clock fallback when idle (D8). The HTTP shipper (`etwship.py`) remains for replay only.
- Collection lab: VMs on the home Proxmox hypervisor (`infrastructure/` Terraform builds them, `src/ansible/` provisions them, cloud-portable
  via inventory). VNet `it2102` 10.21.3.0/24: Windows Server 2022 `ws-etw-0` (.10, DVWA) and `ws-etw-1` (.11, collector only), each running
  `etwcollect` on Kernel-Process, Kernel-File, Kernel-Network; `ubuntu-trainer` (.20: 8 cores, 12 GB, 50 GiB swap; Loki, Grafana, Prometheus,
  training, live server); `nt2102-vpn` (.50). VNet `it2102k` 10.21.4.0/24: Kali `kali` (.30, attacker; `kali.yml` → role `kali_attack`).
  Terraform rules: the user runs `apply`/`destroy`, Claude only writes config and runs `plan`; values are hardcoded in `main.tf` locals and
  only credentials are variables (`TF_VAR_*`); `terraform.tfstate` is committed (public repo, no secrets). A CPU or RAM change reboots the VM.
  Windows WinRM creds are hardcoded in `proxmox.ini` by choice (user, 2026-11-06: reachable from the home LAN and VPN, not the internet).
- Target schema (NODLINK W10, as the repo expects): 3 node types `PROCESS`, `FILE`, `NET`
  (repo `database_config.py`), 6 edge types (Write, Read, Send, Recv, Start, Load), epoch-millisecond
  timestamps.
- Node identity: processes keyed by PID + start time (raw PIDs get reused and would corrupt idle-time
  features); FILE and NET nodes keyed by a hash of path / ip:port so IDs are stable across train and test.
  Windowed outputs prefix every node ID with its window (`w0007_...`).
- Benign set: **idle machine activity, pulled directly from Loki**, no capture script. A clean idle range is pulled with
  `lab pull --start <t> --end <t> --split test --host <vm>` and trained on. Why idle: a scripted workload reuses the attack's living-off-the-land
  binaries and makes the attack in-distribution. Keep benign demo windows idle: an idle model is thin, so real activity can false-positive
  (D2). Exception (user, 2026-11-12): `ws-etw-1`'s baseline includes its owner's setup and RDP sessions (D2). How to train: `docs/notes/training-runbook.md`.
- Windowing: an idle benign time-range pulled from Loki, cut into 5-min sliding windows with a 2-min
  stride. Detection uses the same 5-min window length. For the small demo, tumbling windows
  (stride = window) are enough; sliding windows are the option for real attacks that cross window
  boundaries.
- Feature extraction: a standalone re-implementation of the repo's behavior-feature step (paper
  §5.1.2) that needs no RDF/GraphDB/PyG, so features can be inspected by EDA. 15 features per node:
  12 L2-normalised out/in action counts + 3 min-max-scaled idle-time stats. The scaler is fit on
  train and reused for test/live windows.
- Trainer: own minimal OCRGCN (PyG `RGCNConv` encoder + Deep-SVDD-style hypersphere loss) instead of the authors' PyGOD fork, so every line is
  explainable. Paper hyperparameters (3 layers by default, 32-d, lr 0.005, β 0.5); exp08 and exp09-ws1 use `--layers 2`. Validation holds out
  whole windows and purges the overlapping ones to avoid leakage.
- Ground truth: the injected RCE kill chain self-logs a label JSONL, joined to nodes by indicator + time window (D6).

### Pipeline map (where each stage lives)

- VM side (Ansible roles in `src/ansible/roles/`): `win_base`, `win_collector` (uv + `src/etw-python` collector as SYSTEM task
  `etwlab-collector`, `etwctl`, copies `src/pipeline/etw2nodlink.py` / `etwwindow.py` / `src/live/etwship.py`; the collector source is a git clone of
  this repo), `win_shipper` (`start-live.ps1` watchdog + SYSTEM task `etwlab-live`: always-on live capture), `win_vulnlab` (XAMPP + DVWA).
  The Linux endpoint is retired (D11): `linux_auditd`, `linux_auditship`, `target.yml` and `src/audit-python` stay unused.
- Trainer monitoring: role `ubuntu_monitoring` (tag `monitoring`): Prometheus + node-exporter + cAdvisor in `/opt/monitoring`,
  127.0.0.1 only, Grafana dashboard "Trainer resources". The same role caps everything run over SSH, training included
  (`user.slice`, 50 % RAM), and reserves resources for monitoring; 50 GiB swap in `ubuntu_base` (`src/ansible/README.md`).
- Proxmox infrastructure: `infrastructure/` (`main.tf` SDN zone/VNet/subnet, ws-etw-0 and ws-etw-1 from the Server 2022
  eval ISO with manual install, ubuntu-trainer and nt2102-vpn from a cloud image + cloud-init, per-VM
  firewall rules prefixed `it2102:`; `README.md`). Then Ansible with `-i proxmox.ini`; `proxmox-firewall.yml`
  adds the pve-master DNAT (TCP 2102 → nt2102-vpn:1194).
- Improve loop: `lab` CLI (`src/lab/cli.py`, console script; `source .venv/bin/activate`): `lab loop` = pull + build → features → train into one
  run folder; `lab train --exclude-file` (leave windows out); `lab compare` (two models on the same replayed windows); `lab promote <run> [--host <vm>]`
  sets the pointer (`src/artifacts/current.json`, overridden per machine by `current.local.json`) that `live_server.py`, `serve.sh`, `rejudge.sh`
  and `ocr-live` read; `lab hosts <vm> ...` picks the scored VMs; `lab flows` records known traffic between VMs.
- Attack side: `src/atomic-attack-pack/`: one RCE kill chain (`scenarios/rce-killchain.ps1`) delivered from Kali by `tools/launch_exploit.py` (pack
  at `/opt/attack-pack`, role `kali_attack`): DVWA exploit → Discovery → T1046 scan → T1105 real-tool download + run → DB dump → C2 beacon → T1053.005 →
  T1070 cleanup. `LABELING.md`, `tools/build_node_labels.py`.
- Collection via Loki (trainer): `src/pipeline/loki_pull.py` (Loki → `<exp>/raw/`) → `build_experiment.py` (convert → window → check →
  `experiment.json`); shared client `lokiclient.py`.
- Host side: `src/pipeline/encode_to_features.py` (§5.1.2 features) → `train_gnn_models.py` (OCRGCN per node type) →
  `score_window.py` (score any edge CSV with the train scaler + relations).
- Two VMs: one `engine.py` + `loki_source.py` per scored VM (`lab promote --host`, `lab hosts`), then `src/live/correlate.py` (flows
  between VMs + red windows → link / suspected / confirmed; known traffic from `lab flows`, host IPs from `/etc/nt2102-hosts.json`).
- Live (host): `src/live/loki_source.py` (follows Loki) → `src/live/live_server.py` (FastAPI + SSE UI) → `engine.py` (offsets, event-time
  reorder, windows, scoring thread) → `src/pipeline/alg1.py` (Alg. 1 + SG-Det, also re-judges saved
  sessions) → `src/pipeline/alg2.py` (optional LLM thread: triage verdict + Alg. 2 l.3–15 report on red
  windows) → `src/live/history.py` (persistent window history, `sessions/history.jsonl`, range queries via
  `/api/history`); `etwship.py --replay` + `compare_replay.py` check the live path against the offline
  pipeline. Run guide: `src/live/README.md`.
- Artefacts: `src/out/etw/experiments/<id>/` (raw, converted, windowed edges, `windows.csv`,
  `experiment.json`, `check.json`, `pull.json`, benign ledger in `labels/`), `src/artifacts/runs/<run>/`
  (one model: `models/`, `features/` + scaler, `experiment.json`, `lineage.json`; `exp01` predates this and
  uses `src/artifacts/features/self-dataset/`), `src/artifacts/features/paper/` (NODLINK W10), live sessions
  `src/live/sessions/<id>/` (raw copy + per-window CSV/JSON; gitignored). NODLINK data: `dataset/nodlink/`.
- EDA notebooks: `src/notebooks/01-silketw-raw` … `05-features-paper` (numbered in reading order).
- Explainer page (edges → features): `docs/explainers/`.
- Deliverables (Vietnamese text, technical terms kept in English):
  - Report: `latex/` (`main.tex` title page + preamble, `contents.tex` body, `ref.bib`; build with
    `latexmk -pdf main.tex`).
  - Slides: `presentation/` (Slidev; published to GitHub Pages on push to `main` by `.github/workflows/slides.yml`, images in `images/` referenced as `./images/...` so the `/NT2102/` base applies; `slides.md`; Vue diagrams in `components/`: `MessagePassing`,
    `StackedLayers`, `RgcnLayer` (one-layer animation), `HiddenLayers`, `Hypersphere`, `OurPipeline`, `Windows`, `AttackGraph`,
    `BehaviorCounts` (graph + exact out_/in_ feature table), `LabInfra`, `KillChain`; Q&A backup in `note.txt`). `StackedLayers`, `HiddenLayers` and `Windows` are kept
    but unused since the 15-slide cut. `npm run dev` /
    `npx slidev build`; check layout with `npx slidev export --format png [--range N] [--with-clicks]`. Max 15 slides (currently 13), keywords only;
    explanations go to the report and speaker notes. Deck redesigned
    2026-11-08 (user structure: intro → core concept → demo → thanks; results + limits live in the report; one Windows VM; no source-tag chips; ≤3 bullets/group;
    video placeholder on the "3 · Video demo" slide for the user to fill). Slides cross-reference by name ("slide ② Behavior feature"), not number.
- Root shortcuts: `selftest.sh`, `serve.sh`, `replay.sh`, `rejudge.sh` (wrap the documented commands).
- Root `README.md`: front door (project, quick start, layout, references). `src/README.md`: map of
  `src/` and the new-member start path.

## 10. Known deviations

Full entries (what / why / impact / claims limited) live in `docs/DEVIATIONS.md`. Summary:

- Windowed, near-live detection instead of the paper's offline whole-log prediction.
- Own Windows ETW data instead of NODLINK/DARPA datasets; benign = idle machine activity (D2).
- Own Python ETW collector (`src/etw-python`) instead of SilkETW: SilkETW-format lines with plain values,
  self/collector I/O excluded in the callback, pushes to Loki itself (D10).
- Feature extraction: all 15 columns kept; idle scaler fit on train only; RDF skipped; per-window
  nodes give correlated rows.
- Graph storage: no RDF/GraphDB; edges stay CSV and Alg. 1 runs in memory per window.
- Trainer: own OCRGCN re-implementation (loss/threshold details are [INFERENCE] from upstream PyGOD),
  window-based validation split, patience-based early stop, deterministic seeds.
- Labels: indicator + time oracle from injected attacks, not analyst ground truth.
- Live inference: clock-aligned tumbling windows, partial windows not scored, shipper PID excluded,
  no cross-window dedup. Alg. 1 runs in memory per window with the [AUTHOR CODE] subgraph score (Σ clamped
  min-max prob, not the paper text's "sum of scores"), [PAPER] §7.2.1 level scale (Moderate from 10; [AUTHOR CODE]
  > 10; the [OURS] 30 was retired 2026-11-03), no top-k fallback, [OURS] NET rule (a kept subgraph needs >= 1 anomalous
  NET node, else suppressed and logged; 2026-11-09). Window history persisted to files (history.jsonl).
- Two hosts: one model per host (D12); cross-host correlation is [OURS] (D13), the paper leaves lateral movement out of scope.
- LLM step: [OURS] triage verdict before the paper's Alg. 2 prompts (the paper never classifies), no RAG,
  Alg. 2 l.16–25 left out, verdict is an overlay and never training data (D9).

## 11. Status, open items, next steps

Live in `docs/PROGRESS.md` (single source; do not duplicate here).

## 12. Roadmap

Concept order (Track A): 1 provenance graphs ✓ → 2 GNN basics (message passing ✓, GCN, homogeneous
loss) → 3 RGCN (per-relation W_r, basis decomposition, normaliser c_{i,r}) → 4 one-class graph
learning (Deep SVDD/OCGNN hypersphere, β, contamination; vs sklearn OCSVM) → 5 behavior features
(§5.1.2) → 6 per-node-type models → 7 anomalous subgraph construction (Alg. 1) → 8 LLM investigator
(Alg. 2, App. B) → 9 evaluation (node-level P/R/F1, 2-hop vs strict metric, Table 6, class imbalance).

Reproducing the paper's numbers (NODLINK, DARPA) is out of scope (section 8). NODLINK W10 features exist in `src/artifacts/features/paper/`.
