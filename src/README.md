# src/: the OCR-APT demo lab

Everything for the NT2102 demo ("Phát hiện bất thường trên endpoint device log"): detect a demo attack
(e.g. CVE exploit → RCE → ATT&CK techniques) live on a Windows host, using OCR-APT's core method.

- **Windows Server lab VM** (`ws-etw`): records kernel activity with ETW (SilkETW); Grafana Alloy ships it to Loki.
- **Ubuntu trainer** (`ubuntu-trainer`): Loki + Grafana, then logs → graph → behaviour features → one-class
  GNN per node type (OCRGCN).
- **Live demo**: scores every 5-minute window, builds alert subgraphs (Alg. 1), optional LLM verdict, web UI.

Status: `../docs/PROGRESS.md`. Differences from the paper: `../docs/DEVIATIONS.md`.

## The improve loop: `lab`

One command walks the whole loop. Set it up once, from the repo root:

```bash
uv sync                          # also installs the `lab` command into .venv/bin
source .venv/bin/activate        # `lab` is now on PATH (without activating: uv run lab ...)
```

Three words, used consistently by every `lab` option:
- **capture**: what the VM recorded, one run in Loki (`x-<UTC>` from `07`, `live-<UTC>` from `start-live.ps1`).
- **experiment**: a capture pulled and built, in `out/etw/experiments/<capture>/`.
- **model**: features plus a trained OCRGCN, in `artifacts/runs/<model>/`.

```bash
lab                              # overview; `lab <command> -h` = all options + examples
lab status                       # current model, experiments (provider coverage), models
lab loop                         # capture -> experiment -> model (interactive: pick the capture, name the model)
lab loop --latest --model exp02 --yes                        # the same, no questions: newest train capture
lab train --experiment x-20261017T030000Z --model exp03      # retrain an experiment you already have
lab promote exp02                # make it the model the live server loads
sudo systemctl restart ocr-live  # (on the trainer) load it
```

| step | stage script (writes standalone files you can inspect) | writes |
|---|---|---|
| pull + build | `pipeline/loki_pull.py` → `pipeline/build_experiment.py` | `out/etw/experiments/<id>/`: raw, converted and windowed edges, `windows.csv`, `check.json`, `experiment.json` |
| features | `pipeline/encode_to_features.py` | `artifacts/runs/<model>/features/` (the scaler is fit on this train data) |
| train | `pipeline/train_gnn_models.py` | `artifacts/runs/<model>/models/`, `train_meta.json`, `train_scores.csv` |
| record | `lab` | `artifacts/runs/<model>/experiment.json` (a copy) + `lineage.json` (experiment, commands, git commit) |
| promote | `lab` | `artifacts/current.local.json`, the current-model pointer |

**One folder = one model.** Everything the live server needs is inside `artifacts/runs/<model>/`, and a
trained model is never overwritten.

**Current model:**
- `artifacts/current.local.json` if it exists (per machine, gitignored, written by `lab promote`);
- otherwise `artifacts/current.json` (in git).

`live_server.py`, `serve.sh`, `rejudge.sh` and the `ocr-live` service all read it, so a new model is one
`lab promote` away.

## The folders at a glance

| folder | kind | runs where | in git |
|---|---|---|---|
| `Vagrantfile`, `setup-virtualbox.sh` | builds the two bare lab VMs (Windows collector + Ubuntu trainer) | host | yes |
| `ansible/` | provisions both VMs (Windows: SilkETW, Alloy, vulnlab; Ubuntu: Loki/Grafana, repo, `ocr-live`); cloud-portable via inventory | control node | yes |
| `lab/` | the `lab` CLI (the loop above) | trainer / host | yes |
| `pipeline/` | stage scripts: Loki pull, convert, window, features, training, scoring, Alg. 1/2 | trainer / host | yes |
| `live/` | live demo: server, engine, Loki source, web UI, replay tools | trainer | yes (`sessions/` no) |
| `scripts/` | the two PowerShell scripts Ansible still deploys: `06-benign-workload.ps1`, `07-build-train-dataset.ps1` | VM | yes |
| `artifacts/` | data we keep: trained runs (each with its features), the current-model pointer, paper features | — | yes |
| `out/` | local data: experiments pulled from Loki, raw captures | trainer / host | no |
| `atomic-attack-pack/` | attack scenarios + labelling scheme | VM (isolated) | yes |
| `notebooks/` | EDA notebooks, numbered in reading order | Jupyter | yes |

## Start here (new member)

Read in this order (about 30 minutes):

1. `../docs/reports/professor.md`: the 5-minute overview, with architecture diagrams.
2. `../docs/notes/data-flow.md`: how Windows logs become the model's input, end to end (raw events → edges → windows → features → tensors).
   Then, in a browser: `../docs/explainers/edges-to-node-features.html` (edges → the 15 node features) and
   `../docs/explainers/live-windows.html` (how one live window is scored; windows vs stride).
3. `../docs/reports/teammate.md`: how to run things, known pitfalls, open tasks.
4. `ansible/README.md` (the two VMs) and `live/README.md` (the live demo).

Then pick a path. All commands run from the **repo root** after `uv sync`; `lab` also needs
`source .venv/bin/activate`.

| you want to… | needs a VM? | do this |
|---|---|---|
| see that the code runs | no | `./selftest.sh` (each step proves "it runs", not "it detects attacks") |
| look at the data | no | `uv run jupyter lab`, then open `src/notebooks/` (start with `04-features-self/`) |
| understand the model | no | read `pipeline/train_gnn_models.py` top to bottom (shapes in comments) |
| see what you have | no | `lab status` |
| watch the demo without the Windows VM | trainer only | feed Loki from a recording: `live/README.md` step 5 (`loki_push.py`) |
| retrain | trainer | `lab loop`, then `lab promote <model>` |
| run the real live demo | both | `ansible/README.md`, then `live/README.md` step 5 |

`out/` and `live/sessions/` are not in git. To replay or retrain on our first capture, ask a teammate for a
copy of `out/etw/experiments/20261009T145632Z/`.

## What is in each folder

```
src/
├── Vagrantfile               the two bare VMs: ws-etw (Windows Server 2022, 10.21.2.10, 3D off) and
│                             ubuntu-trainer (Ubuntu 24.04, 10.21.2.20); no provisioners (Ansible does that)
├── setup-virtualbox.sh       one-time host prep: allow 10.21.2.0/24 in VirtualBox, warn about KVM
├── ansible/                  site.yml / windows.yml / ubuntu.yml, roles/, inventories (ansible/README.md)
├── lab/cli.py                the `lab` command (status, pull, train, loop, promote, current, selftest)
├── pipeline/                 stage scripts (Python, code only; each has --selftest)
│   ├── lokiclient.py           Loki HTTP calls (+ fake Loki for selftests)
│   ├── loki_pull.py            Loki -> experiment raw/ (interactive picker or flags)
│   ├── build_experiment.py     raw/ -> converted, windowed edges, check.json, experiment.json
│   ├── etw2nodlink.py          SilkETW JSON -> edges (the converter; Ansible copies it to the VM too)
│   ├── etwwindow.py            edges -> 5-min windows;  check_rdf_input.py  input-contract check
│   ├── encode_to_features.py   edges -> 15 behaviour features per node (paper §5.1.2)
│   ├── train_gnn_models.py     trains one OCRGCN per node type
│   ├── score_window.py         scores any edge CSV with a trained run (shared with the live demo)
│   ├── alg1.py                 Alg. 1 subgraphs + subgraph-score filter (in memory); re-judges sessions
│   └── alg2.py                 LLM triage + Alg. 2 attack report on red windows (optional)
├── live/                     live demo (live/README.md)
│   ├── live_server.py          FastAPI server: --loki-url, /ingest, /api/*, SSE, UI at /
│   ├── loki_source.py          live input: follows the newest test run in Loki
│   ├── engine.py               reorder by event time, convert, cut windows, score, call alg1/alg2
│   ├── loki_push.py            test feed: pushes a recorded run into Loki in Alloy's shape
│   ├── etwship.py              HTTP --replay (for compare_replay) and --print-dos-map (etwctl uses it)
│   ├── compare_replay.py       checks that live results equal the offline pipeline
│   ├── static/index.html       the web UI
│   └── sessions/<id>/          per-session outputs (not in git)
├── scripts/                  06-benign-workload.ps1, 07-build-train-dataset.ps1 (deployed to C:\scripts)
├── artifacts/                data we keep in git
│   ├── current.json            the current-model pointer (current.local.json overrides it, not in git)
│   ├── runs/<model>/           one model: models/, features/, experiment.json, lineage.json, train_*.csv
│   ├── runs/exp01/             the first model (Win10 data); its features are in features/self-dataset/
│   └── features/               self-dataset/ (exp01's features + scaler), paper/ (NODLINK W10)
├── out/                      local data (not in git)
│   ├── etw/experiments/<id>/   one experiment: raw/, converted/, windowed edges, windows.csv,
│   │                           experiment.json, check.json, pull.json (from Loki), labels/ledger.csv
│   ├── etw/raw/{train,test}/   raw SilkETW captures (VM side)
│   └── _old/                   early test captures and an un-windowed capture; do not train on them
├── atomic-attack-pack/       10 attack scenarios (Atomic Red Team + custom), LABELING.md, tools/
└── notebooks/                EDA: 01-silketw-raw … 05-features-paper
```

`artifacts/runs/exp01/train_meta.json` and `artifacts/features/*/feature_scaler.json` record the input
paths as they were at training time. They are a record of that run; do not edit them.

## Data flow

1. VM: `scripts/07` (training) or `start-live.ps1` (demo) captures; Alloy ships every line to Loki on the trainer.
2. Trainer: `lab loop` runs `loki_pull.py --build` → `encode_to_features.py` → `train_gnn_models.py`, all into
   `artifacts/runs/<model>/`. Then `lab promote <model>`.
3. Live: Loki → `live/live_server.py --loki-url` (current model) → `live/sessions/<id>/` + web UI.

The converter (`pipeline/etw2nodlink.py`) is the same file on the VM and the trainer. `experiment.json`
records its sha256 and the live server compares it (`converter_check`). Edit it before a capture, never after.

## Pitfalls

| symptom | cause → fix |
|---|---|
| collapsed model, all-zero features | trained on un-windowed edges (e.g. `out/_old/etw/SimulatedW10_train_edges.csv`) → train on an experiment folder (`lab train --experiment`) |
| a provider covers only a minute of the capture | `lab status` warns (e.g. `kfile 1/21m` in `20261009T145632Z`) → check the collector before training on it |
| installer activity flagged in a "benign" test | software installs are not benign workload → use `06-benign-workload.ps1` |
| VM dies, looks like OOM | VMSVGA segfault in `libnvidia-glcore` (3D acceleration) → keep `accelerate3d off`; check `journalctl -k` and `VBox.log.1` first |
| `converter_check: MISMATCH` | the model was trained with another converter version → rebuild + retrain (`lab loop`) |
| live server loads the wrong model | `lab current` shows which pointer is used (`current.local.json` wins) |
