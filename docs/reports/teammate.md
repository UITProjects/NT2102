# NT2102 team handoff (status 2026-10-13)

Practical start for a teammate. Status lives in `docs/PROGRESS.md`, differences from the paper in
`docs/DEVIATIONS.md`, the folder map and pitfalls in `src/README.md`. This page only points the way.

## 1. What the project is

- **Goal:** catch a demo attack (e.g. CVE exploit → RCE → ATT&CK techniques) live on a Windows 10 VM,
  from kernel audit logs, while benign windows stay green. Not limited to APTs.
- **Method:** OCR-APT's core idea (CCS 2025), not a full reproduction. One one-class RGCN per node
  type (process / file / network) on 15 behaviour features, trained on benign data only.
- **Live path:** VM ships raw ETW lines → host builds a 5-min window graph → scores nodes → Alg. 1
  subgraphs → window is red only if a subgraph's score (Σ clamped min-max prob of its anomalous nodes)
  reaches Moderate (≥ 30, our scale) → optional LLM verdict + report. Diagram: `src/live/README.md`.

## 2. How to run (repo root, after `uv sync`)

| want | command |
|---|---|
| prove the code runs | `./selftest.sh` |
| live server for the VM / for a replay | `./serve.sh` / `./serve.sh local` |
| replay a recorded capture | `./replay.sh <session-id\|raw-dir>` |
| re-judge a saved session (cut-off, LLM) | `./rejudge.sh <session-id> [llm-provider]` |
| build the VM | `src/ansible/README.md` |
| train data → model | `07-build-train-dataset.ps1` (VM) → `encode_to_features.py` → `train_gnn_models.py` (`src/README.md` → Data flow) |

Step-by-step live runbook (replay check, live with the VM, alert rule, outputs): `src/live/README.md`.
Before you touch anything, read the pitfalls table at the end of `src/README.md`.

## 3. What to work on

The current "Next 3 steps" are in `docs/PROGRESS.md`. In short:

1. **More benign data**: 2–3 captures at different times (trigger the Google updater, a Defender scan),
   retrain into `src/artifacts/runs/exp02`, and record the max benign subgraph score per window.
2. **Replay check**: `src/live/README.md` step 4 → `ALL MATCH`.
3. **Attack demo**: one Atomic scenario during a live session (`src/atomic-attack-pack/`). Does its window
   reach ≥ 30? Do not re-tune the cut-off on the attack window.
4. **CVE → RCE scenario** (target demo, not built yet): exploit a lab service, then ATT&CK follow-on.

Smaller open items (code bugs and data checks) are under PROGRESS "Open items".
