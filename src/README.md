# src/: the OCR-APT demo lab

Everything for the NT2102 course demo of OCR-APT ("Phát hiện bất thường trên endpoint device log"):

- a **Windows 10 lab VM** that records kernel activity with ETW (SilkETW);
- a **host pipeline** that turns those logs into a graph, computes behaviour features and trains
  one-class graph neural networks (OCRGCN);
- a **live demo** that scores every 5-minute window and shows alerts in a web UI.

The end goal is a live demo in which a real attack raises a true alert while benign windows stay green.
For project status read `../docs/PROGRESS.md`; for differences from the paper read `../docs/DEVIATIONS.md`.

## Start here (new member)

Read in this order (about 30 minutes):

1. `../docs/reports/professor.md`: the 5-minute overview, with architecture diagrams.
2. `ocrapt-edges-to-node-features-shared.html` (open it in a browser): how graph edges become the 15 node features.
3. `../docs/reports/teammate.md`: how to run things, known pitfalls, open tasks.
4. `live/README.md`: the live demo step by step.

Then pick a path. All commands run from the **repo root**, with Python managed by
[uv](https://docs.astral.sh/uv/) (`uv sync` once).

| you want to… | needs the VM? | do this |
|---|---|---|
| see that the code runs | no | the selftests below |
| look at the data | no | `uv run jupyter lab`, then open `features/self-dataset/feature-exploranation.ipynb` (our data) or `features/paper/eda-for-features-of-paper.ipynb` (paper's NODLINK data) |
| understand the model | no | read `pipeline/train_gnn_models.py` top to bottom; its docstring explains every step |
| watch the demo without a VM | no, but needs a capture folder | replay a recorded capture: `live/README.md` step 4 |
| run the real live demo | yes | build the VM (below), then `live/README.md` step 5 |

Selftests (each proves "it runs", not "it detects attacks"):
```
uv run python src/pipeline/train_gnn_models.py --selftest
uv run python src/pipeline/score_window.py --selftest
uv run python src/live/engine.py --selftest
uv run python src/live/etwship.py --selftest
```

`out/` and `live/sessions/` are not in git. To replay or retrain on our capture, ask a teammate for a
copy of `out/etw/experiments/20261009T145632Z/`.

## What is in this folder

```
src/
├── Vagrantfile               Windows 10 VM "w10-etw": 8 GB, 4 CPU, 3D acceleration off,
│                             host-only IP 10.21.2.10, runs provisioners 01–04
├── setup-virtualbox.sh       one-time host prep: allow 10.21.2.0/24 in VirtualBox, warn about KVM
├── virtualbox-setup.sh       identical copy of setup-virtualbox.sh (to be removed)
├── compose.yaml              GraphDB 10.8.0 (Docker, 10.21.3.0/24); not needed for the live demo
├── scripts/                  PowerShell provisioners run inside the VM (table below)
├── out/                      VM ↔ host data exchange (not in git)
│   ├── etw/experiments/<id>/   one capture: raw logs, converted edges, 5-min windowed edges,
│   │                           windows.csv, experiment.json, check.json
│   ├── etw/raw/{train,test}/   raw SilkETW captures
│   └── loose files             early test captures (kfile/knet/kproc.json, fileio_nofilter.json),
│                               lab-versions.json (installed tool versions); SimulatedW10_* here are an
│                               OLD capture without windows, do not train on them
├── features/
│   ├── self-dataset/         our features: features_raw.csv, features_model.csv (model input),
│   │                         feature_scaler.json (fit on train, reused live), EDA notebook
│   └── paper/                the same features computed on the paper's NODLINK Simulated W10 data
├── pipeline/                 host pipeline (Python)
│   ├── w10_to_rdf.py           edges → RDF/TSV for GraphDB (only for later Alg. 1 work)
│   ├── encode_to_features.py   edges → 15 behaviour features per node (paper §5.1.2)
│   ├── train_gnn_models.py     trains one OCRGCN per node type → runs/<exp>/
│   ├── score_window.py         scores any edge CSV with a trained run (shared with the live demo)
│   └── runs/exp01/             the current trained model (models/*.pt, thresholds, scores)
├── live/                     live demo (see live/README.md)
│   ├── etwship.py              shipper: tails ETW files on the VM and POSTs them; --replay on the host
│   ├── engine.py               reorder by event time, convert, cut windows, score, build subgraphs
│   ├── live_server.py          FastAPI server: /ingest, /api/*, live updates, UI at /
│   ├── static/index.html       the web UI
│   ├── compare_replay.py       checks that live results equal the offline pipeline
│   ├── sync_ps1.py             copies etwship.py into scripts/08-live-shipper.ps1
│   └── sessions/<id>/          per-session outputs (not in git)
├── atomic-attack-pack/       10 attack scenarios (Atomic Red Team + custom), run-all / cleanup
│                             scripts, LABELING.md, tools/build_node_labels.py
├── exploration/              early EDA notebooks: paper/ (NODLINK), self-data-collection/, SilkETW/
└── ocrapt-edges-to-node-features-shared.html   explainer page: edges → node features
```

## VM provisioners (`scripts/`)

| script | what it does |
|---|---|
| `01-base.ps1` | base Windows setup: network profile, UTC clock, no sleep, RDP, lab folders, Windows Update frozen, Defender exclusions |
| `02-tools.ps1` | installs pinned tools: SilkETW, Invoke-AtomicRedTeam + atomics |
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
2. Host: `pipeline/encode_to_features.py` → `features/self-dataset/`.
3. Host: `pipeline/train_gnn_models.py` → `pipeline/runs/<exp>/`.
4. Live: `scripts/08` on the VM → `live/live_server.py` on the host → `live/sessions/<id>/` + web UI.

## Pitfalls

- Train only on edges from `out/etw/experiments/<id>/`. The loose `out/etw/SimulatedW10_train_edges.csv`
  has no windows and produced a collapsed model once.
- For benign live tests run `06-benign-workload.ps1`, not `03` (installer activity gets flagged).
- After editing `live/etwship.py`, run `uv run python src/live/sync_ps1.py`, or the VM keeps the old copy.
- Keep 3D acceleration off in the VM: with it on, the NVIDIA driver crashed VirtualBox.
