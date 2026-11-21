# src/: code

Everything that runs: the collector on the Windows VMs, the training pipeline and live detector on the trainer,
the attack, and the provisioning.

## Architecture

```
VM       etw-python/ (etwcollect) ─► Loki
trainer  pipeline/: loki_pull → build_experiment (graph, windows) → encode_to_features (15 per node)
                    → train_gnn_models (one OCRGCN per node type) → artifacts/runs/<model>/
         live/: Loki → engine (score windows, alg1 subgraphs, alg2 LLM) → web UI + history
         lab/: the `lab` CLI that drives pull → build → features → train → promote
```

One folder per trained model (`artifacts/runs/<model>/`: models, features + scaler, lineage); a model is never
overwritten. The converter (`pipeline/etw2nodlink.py`) is the same file on the VM and the trainer, and its
sha256 is checked at load.

## Folders

| folder | what |
|---|---|
| `etw-python/` | ETW collector |
| `pipeline/` | Loki pull, converter, windowing, features, training, scoring, Alg. 1, Alg. 2 |
| `live/` | live detector, web UI, snapshot playback |
| `lab/` | `lab` CLI |
| `artifacts/` | trained models, current-model pointer, paper features |
| `atomic-attack-pack/` | RCE kill chain |
| `ansible/` | lab provisioning |
| `notebooks/` | EDA, numbered in reading order |
| `audit-python/` | retired auditd shipper |

Every pipeline module has a `--selftest`; `./selftest.sh` runs them all. They prove the code runs, not that it
detects attacks.
