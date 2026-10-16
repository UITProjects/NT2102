# src/: the OCR-APT demo lab

Everything for the NT2102 demo ("Phát hiện bất thường trên endpoint device log"): detect a demo attack
(e.g. CVE exploit → RCE → ATT&CK techniques) live on a Windows host, using OCR-APT's core method.

- **Windows 10 lab VM**: records kernel activity with ETW (SilkETW).
- **Host pipeline**: logs → graph → behaviour features → one-class GNN per node type (OCRGCN).
- **Live demo**: scores every 5-minute window, builds alert subgraphs (Alg. 1), optional LLM verdict, web UI.

Status: `../docs/PROGRESS.md`. Differences from the paper: `../docs/DEVIATIONS.md`.

## The folders at a glance

Each top-level folder holds one kind of thing:

| folder | kind | runs where | in git |
|---|---|---|---|
| `Vagrantfile`, `setup-virtualbox.sh` | VM lab definition (now two VMs: Windows collector + Ubuntu trainer) | host | yes |
| `ansible/` | provisions both VMs (Windows collector/shipper/vulnlab + Ubuntu trainer); cloud-portable via inventory | host | yes |
| `scripts/` | legacy PowerShell provisioners (pre-Ansible). `06-benign-workload.ps1` + `etwctl.ps1` are still deployed by Ansible; the rest are reference | VM | yes |
| `out/` | data the VM writes (raw ETW, converted and windowed edges); shared with the VM as `C:\lab-out` | VM → host | no |
| `atomic-attack-pack/` | attack scenarios + labelling scheme | VM (isolated) | yes |
| `pipeline/` | host code: edges → features → OCRGCN training → scoring | host | yes |
| `live/` | host code: live demo server, engine, shipper, web UI | host (+ shipper on VM) | yes (`sessions/` no) |
| `artifacts/` | outputs we keep: features, scaler, trained runs. Data only, no code | — | yes |
| `notebooks/` | EDA notebooks, numbered in reading order | host (Jupyter) | yes |
| `graphdb/` | GraphDB via Docker Compose; not needed for the live demo | host | yes |

Provisioning moved from the Vagrant PowerShell provisioners to `ansible/` (see `ansible/README.md`): two
VMs — a Windows Server collector that pushes raw ETW only, and an Ubuntu trainer that runs the live
server. The ETW converter is now a standalone file `pipeline/etw2nodlink.py` (the source of truth
`live/engine.py` imports and hashes; Ansible deploys the same file to the VM). The old
`scripts/04-collector.ps1` here-string and `sync_ps1.py` are legacy. Addresses are inventory-driven, so
the lab moves to cloud VMs without code changes.

## Start here (new member)

Read in this order (about 30 minutes):

1. `../docs/reports/professor.md`: the 5-minute overview, with architecture diagrams.
2. `../docs/notes/data-flow.md`: how Windows logs become the model's input, end to end (raw events → edges → windows → features → tensors).
   Then, in a browser: `../docs/explainers/edges-to-node-features.html` (edges → the 15 node features) and
   `../docs/explainers/live-windows.html` (how one live window is scored; windows vs stride).
3. `../docs/reports/teammate.md`: how to run things, known pitfalls, open tasks.
4. `live/README.md`: the live demo step by step.

Then pick a path. All commands run from the **repo root**, with Python managed by
[uv](https://docs.astral.sh/uv/) (`uv sync` once).

| you want to… | needs the VM? | do this |
|---|---|---|
| see that the code runs | no | the selftests below |
| look at the data | no | `uv run jupyter lab`, then open `src/notebooks/` (start with `04-features-self/`) |
| understand the model | no | read `pipeline/train_gnn_models.py` top to bottom (shapes in comments) |
| watch the demo without a VM | no, but needs a capture folder | replay a recorded capture: `live/README.md` step 4 |
| run the real live demo | yes | build the VM (below), then `live/README.md` step 5 |

Selftests: `./selftest.sh` (each proves "it runs", not "it detects attacks").

`out/` and `live/sessions/` are not in git. To replay or retrain on our capture, ask a teammate for a
copy of `out/etw/experiments/20261009T145632Z/`.

## What is in each folder

```
src/
├── Vagrantfile               Windows 10 VM "w10-etw": 8 GB, 4 CPU, 3D acceleration off,
│                             host-only IP 10.21.2.10, runs provisioners 01–04
├── setup-virtualbox.sh       one-time host prep: allow 10.21.2.0/24 in VirtualBox, warn about KVM
├── scripts/                  PowerShell provisioners run inside the VM (table below)
├── out/                      VM ↔ host data exchange (not in git)
│   ├── etw/experiments/<id>/   one capture: raw logs, converted edges, 5-min windowed edges,
│   │                           windows.csv, experiment.json, check.json
│   ├── etw/raw/{train,test}/   raw SilkETW captures
│   └── loose files             early test captures (kfile/knet/kproc.json, fileio_nofilter.json),
│                               lab-versions.json (installed tool versions); SimulatedW10_* here are an
│                               OLD capture without windows, do not train on them
├── atomic-attack-pack/       10 attack scenarios (Atomic Red Team + custom), run-all / cleanup
│                             scripts, LABELING.md, tools/build_node_labels.py
├── pipeline/                 host pipeline (Python, code only)
│   ├── encode_to_features.py   edges → 15 behaviour features per node (paper §5.1.2)
│   ├── train_gnn_models.py     trains one OCRGCN per node type → artifacts/runs/<exp>/
│   ├── score_window.py         scores any edge CSV with a trained run (shared with the live demo)
│   ├── alg1.py                 Alg. 1 subgraphs + subgraph-score filter (in memory); re-judges sessions
│   ├── alg2.py                 LLM triage + Alg. 2 attack report on red windows (optional)
│   └── w10_to_rdf.py           edges → RDF/TSV for GraphDB (unused by the demo)
├── live/                     live demo (see live/README.md)
│   ├── etwship.py              shipper: tails ETW files on the VM and POSTs them; --replay on the host
│   ├── engine.py               reorder by event time, convert, cut windows, score, call alg1/alg2
│   ├── live_server.py          FastAPI server: /ingest, /api/*, live updates, UI at /
│   ├── static/index.html       the web UI
│   ├── compare_replay.py       checks that live results equal the offline pipeline
│   ├── sync_ps1.py             copies etwship.py into scripts/08-live-shipper.ps1
│   └── sessions/<id>/          per-session outputs (not in git)
├── artifacts/                outputs we keep in git (data only)
│   ├── features/self-dataset/  our features: features_raw.csv, features_model.csv (model input),
│   │                           feature_scaler.json (fit on train, reused live)
│   ├── features/paper/         the same features computed on the paper's NODLINK Simulated W10 data
│   └── runs/exp01/             the current trained model (models/*.pt, thresholds, scores)
├── notebooks/                EDA, numbered in reading order
│   ├── 01-silketw-raw/         raw SilkETW events (process, file)
│   ├── 02-nodlink-ws12/        the paper's NODLINK simulated data (benign, anomaly)
│   ├── 03-self-capture/        our converted capture (edges, node attributes)
│   ├── 04-features-self/       our 15 behaviour features
│   └── 05-features-paper/      the same features on NODLINK W10
└── graphdb/compose.yaml      GraphDB 10.8.0 (Docker, 10.21.3.0/24); not needed for the live demo
```

`artifacts/runs/exp01/train_meta.json` and `artifacts/features/*/feature_scaler.json` record the input
paths as they were at training time (before the move to `artifacts/`). They are a record of that run;
do not edit them.

## VM provisioners (`scripts/`)

| script | what it does |
|---|---|
| `01-base.ps1` | base Windows setup: network profile, UTC clock, no sleep, RDP, lab folders, Windows Update frozen, Defender exclusions |
| `02-tools.ps1` | installs SilkETW, Invoke-AtomicRedTeam + atomics (pin slots exist, still empty: PROGRESS) |
| `03-workload.ps1` | **installs** benign servers (Apache, PostgreSQL, optional Chrome/Java). It is not a benign activity generator |
| `04-collector.ps1` | SilkService config (Kernel-Process / File / Network), `etwctl`, the `etw2nodlink.py` converter |
| `05-collect-for-20-minutes.ps1` | one 20-minute capture + `etwwindow.py` windowing |
| `06-benign-workload.ps1` | scripted benign user activity (seeded); use this for benign tests |
| `07-build-train-dataset.ps1` | one training experiment end to end: capture, convert, window, check |
| `08-live-shipper.ps1` | live capture + embedded `etwship.py`, ships to the host at 10.21.2.1:8000 |

Building the VM: run `./setup-virtualbox.sh` once on the host, then `cd src && vagrant up` (provisioners
01–04 run automatically). While the VM is not isolated, `out/` is shared as `C:\lab-out` and `scripts/`
as `C:\scripts`. With `LAB_ISOLATED=1` (network cut for attack runs), copy scripts in by hand.

## Data flow

1. VM: `scripts/07` captures 20 min of benign activity → `out/etw/experiments/<id>/`.
2. Host: `pipeline/encode_to_features.py` → `artifacts/features/self-dataset/`.
3. Host: `pipeline/train_gnn_models.py` → `artifacts/runs/<exp>/`.
4. Live: `scripts/08` on the VM → `live/live_server.py` on the host → `live/sessions/<id>/` + web UI.

## Pitfalls

| symptom | cause → fix |
|---|---|
| collapsed model, all-zero features | trained on the loose `out/etw/SimulatedW10_train_edges.csv` (no windows) → use edges in `out/etw/experiments/<id>/` |
| installer activity flagged in a "benign" test | `03-workload.ps1` installs software → use `06-benign-workload.ps1` |
| VM dies, looks like OOM | VMSVGA segfault in `libnvidia-glcore` (3D acceleration) → keep `accelerate3d off`; check `journalctl -k` and `VBox.log.1` first |
| VM runs an old shipper | edited `live/etwship.py` without syncing → `uv run python src/live/sync_ps1.py` |
| live server stops receiving after a VM crash | dead connection → restart the server |
| GraphDB killed on big loads | heap 8g > container limit 4g → `GRAPHDB_HEAP` ≤ 3g |
