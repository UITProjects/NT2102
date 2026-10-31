# Live demo: VM → Loki → trainer scoring → browser

```
VM  etwcollect (SYSTEM task) → raw\test\live-<UTC>\{kfile,knet,kproc}.json      (start-live.ps1)
    its shipper thread ──push──► Loki :3100 on the trainer      (src/etw-python; SilkService + Alloy before 2026-10-26)
trainer  live_server.py --loki-url → loki_source.py (poll the newest test run, 1 s)
     → engine.py: reorder by event time → etw2nodlink.Graph (same code as training,
     src/pipeline/etw2nodlink.py) → 5-min windows → train scaler + train relations →
     OCRGCN per type → score > τ → Alg. 1 subgraphs + SG-Det (alert = ≥ 1 subgraph ≥ Moderate)
     → optional LLM verdict (alg2.py) → sessions/<id>/windows/<wid>/ + UI (SSE)
```

| file | role |
|---|---|
| `loki_source.py` | follows Loki: newest test run → engine payloads (synthetic offsets, dos map from `etw-meta`) |
| `loki_push.py` | test feed: pushes a recorded run into Loki in the collector's shape (`--shift-to-now --speed N` for live) |
| `etwship.py` | stdlib HTTP shipper: `--replay` on the host (compare_replay), `--print-dos-map` on the VM (etwctl) |
| `engine.py` | ingest, reorder buffer, windows, scoring thread, calls Alg. 1 and the LLM thread |
| `live_server.py` | FastAPI: `/ingest`, `/api/*`, SSE `/api/stream`, UI at `/` |
| `static/index.html` | UI |
| `compare_replay.py` | checks that the live path reproduces the offline pipeline on the training capture |
| `../pipeline/score_window.py` | scorer shared with offline scoring |
| `../pipeline/alg1.py` | Alg. 1 subgraphs + subgraph-score filter; re-judges saved sessions |
| `../pipeline/alg2.py` | LLM verdict on red windows + Alg. 2 attack report; re-judges saved sessions |

Run everything from the repo root after `uv sync`. Shortcuts: `./selftest.sh`, `./serve.sh [live|local]`,
`./replay.sh <session-id|raw-dir> [speed]`, `./rejudge.sh <session-id> [llm-provider]` (`-h` for usage).

## 1. It runs

`./selftest.sh` → PASS on every step.

## 2. Prerequisite: a valid model

`src/artifacts/runs/exp01` (current valid run, docs/PROGRESS.md). A retrain on wider benign data
(PROGRESS "Next 3 steps" #1) goes next to it as `exp02`. The server refuses a run whose
`runs_summary.csv` shows `emb_std ≈ 0`.

## 3. Offline scorer reproduces the training scores

```
uv run python src/pipeline/score_window.py --run-dir src/artifacts/runs/exp01 \
  --edges src/out/etw/experiments/20261009T145632Z/SimulatedW10_train_edges.csv \
  --scaler src/artifacts/features/self-dataset/feature_scaler.json
```
Expect a flag rate per type ≈ `train_flag_rate` in runs_summary.csv; scores equal train_scores.csv.

## 4. Works correctly: replay the training capture (no VM)

```
./serve.sh local --stride-min 2 --origin-ms 1791557793343        # terminal 1: grid = training w0000, stride 2
./replay.sh src/out/etw/experiments/20261009T145632Z/raw/train/x-20261009T145632Z   # terminal 2: 120x ≈ 10 s
uv run python src/live/compare_replay.py --session src/live/sessions/replay-x-20261009T145632Z \
  --experiment src/out/etw/experiments/20261009T145632Z \
  --features src/artifacts/features/self-dataset/features_model.csv \
  --train-scores src/artifacts/runs/exp01/train_scores.csv
```
Expect `ALL MATCH`. Watch at http://127.0.0.1:8000. The dos-map is inferred from the raw paths
(`\device\harddiskvolume4` → `c:` on this VM). Replay speed: 120 works; 0 hangs, 600 times out.

## 5. Live (etwcollect → Loki, the default)

The trainer runs `ocr-live` (systemd, `src/ansible/roles/ubuntu_project`), which is
`live_server.py ... --loki-url http://127.0.0.1:3100` with the current model (`lab promote <run>`) plus
`live_llm_args` (the LLM flags; set in `inventory.local.ini`, empty = LLM off).

**The capture is always on.** The SYSTEM scheduled task `etwlab-live` (win_shipper role) runs
`C:\scripts\start-live.ps1` at boot and every 5 min: it keeps a `test/live-<UTC>` run capturing, starts a new
run every 6 h, deletes raw `live-*` runs idle for 1 day,. By hand, in an elevated
PowerShell on the VM:
```
C:\scripts\start-live.ps1 -Status     # service, current run, hold, task, last log lines
C:\scripts\start-live.ps1 -Rotate     # fresh live run now = a clean session before a demo
C:\scripts\start-live.ps1 -Pause      # stop live and keep it stopped;  -Resume undoes it
# benign = idle: keep the desktop idle; the always-on capture ships it to Loki, pull an idle range to train
```
The server follows the newest `split="test"` run, and a new run starts a new session. Old `live-*` session
folders on the trainer (each holds a raw copy, ~2 GB/h) are deleted after 12 idle hours (`live_session_keep_hours`; a
session holding a `.keep` file is never deleted). Loki keeps 3 days (`loki_retention`): pull training captures within 3 days.

**LLM on the live server.** `ocr-live` has no terminal, so it cannot prompt for the DeepSeek key. Save it
once on the trainer, then let Ansible add the flags:
```
vagrant ssh ubuntu-trainer
cd ~/NT2102 && uv run --group llm python src/pipeline/alg2.py --ping --llm-provider deepseek   # prompts, saves, answers OK
```
The key lands in `~/.config/nt2102/credentials.json` on the trainer (plain text, mode 600). With
`--llm-debug` in `live_llm_args`, `journalctl -u ocr-live -f` shows every raw LLM response.

**Replay a live session on the laptop** (to check it by hand): copy `src/live/sessions/live-<UTC>/` from the
trainer and the model run folder into `src/live/sessions/live-<UTC>/model/`, then
```
RUN_DIR=src/live/sessions/live-<UTC>/model SCALER=<…>/feature_scaler.json EXPERIMENT=<…>/experiment.json \
  ./serve.sh local --llm-provider deepseek --llm-reasoning-effort none --llm-debug   # terminal 1
./replay.sh live-<UTC>                                                               # terminal 2
```
and compare `windows.csv` / `llm_verdicts.csv` of `replay-live-<UTC>` with the live session.
- Clock: newest event time minus `--lateness-s`. After `--loki-idle-s` (default 2 × lateness) with no new
  lines, it follows host time, so the last window of a stopped capture still closes (DEVIATIONS D8).
- UI tile "pipeline lag": host time minus the newest event.

No VM? Feed Loki from a recording (on the trainer):
```
uv run python src/live/loki_push.py --dir src/out/etw/experiments/<id>/raw/train/<run> \
    --split test --run live-replay1 --shift-to-now --speed 10
```

### Alert rule (Alg. 1 + SG-Det, `src/pipeline/alg1.py`, DEVIATIONS D8)
- Subgraph score = Σ prob over its anomalous nodes; prob = (score − train min) / (train max − train min), clamped to [0, 1].
- Window is red only if ≥ 1 subgraph is ≥ Moderate = score ≥ 30 (our live scale; [AUTHOR CODE] is > 10).
  Subgraphs below the cut-off are still listed (dashed buttons in the UI).
- Flags: `--n-seed 15 --min-nodes 3 --max-edges 5000 --sg-min-level Moderate --sg-moderate-from 30 [--sg-min-score X]`.
- Try another cut-off on a saved session without the VM: `./rejudge.sh <id> "" --sg-moderate-from 10.0001`
  (writes `subgraphs_alg1.{json,csv}` per window).

### LLM verdict on red windows (`src/pipeline/alg2.py`, DEVIATIONS D9)
- Off by default. `--llm-provider anthropic|openai|ollama|deepseek` (first `uv sync --group llm`; keys from
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`; Ollama at `http://localhost:11434/v1` unless `--llm-base-url`).
  `mock` is a keyword rule for UI tests, not an LLM.
- **Any OpenAI-compatible endpoint** (self-hosted vLLM / LM Studio / OpenRouter / Groq / a proxy):
  `--llm-provider openai --llm-base-url http://host:8000/v1 [--llm-model NAME] [--llm-api-key KEY]`.
  The key falls back to `$OPENAI_API_KEY`; a keyless endpoint used with `--llm-base-url` gets a harmless
  dummy key so the SDK is satisfied. Same flags on `rejudge.sh`/`alg2.py` directly.
- **DeepSeek API** (chosen provider; model `deepseek-flash` = DeepSeek-V4.1-Flash-1017, at
  `https://api.deepseek.com`): `--llm-provider deepseek [--llm-reasoning-effort none]`. Get a key at
  platform.deepseek.com; usage and cost show at platform.deepseek.com/usage.
  - Key: `--llm-api-key` > `$NT2102_DEEPSEEK_API_KEY` > `~/.config/nt2102/credentials.json` > a terminal
    prompt (input hidden). Your own unprefixed `DEEPSEEK_API_KEY` is deliberately ignored.
  - **The first prompt saves the key to `~/.config/nt2102/credentials.json` in plain text (mode 600, outside
    the repo).** Delete its `deepseek` entry (or the file) to forget it or enter a new one. No terminal (for
    example a service) → the start fails with a message instead of prompting.
  - `--llm-reasoning-effort none|low|high|max`: not sent by default (DeepSeek thinks, effort high, and ignores
    temperature). `none` turns thinking off: fastest, and temperature 0 applies, so use it for the live demo.
- **Debug:** `--llm-debug` or `NT2102_LLM_DEBUG=1` (also through `rejudge.sh`) prints every call's raw
  response (including DeepSeek's `reasoning_content`) or HTTP error body to stderr and keeps it as `raw` in
  `calls.jsonl`. An answer cut at the provider's output limit always prints a warning.
- Runs on its own thread: neutral triage (attack / benign / uncertain), then the paper's Alg. 2 prompts
  for attacks. The window stays red; the badge shows `LLM: attack`, `LLM: FP` (struck through), `LLM: ?`
  or `LLM: …`.
- UI test: the benign replay `live-20261011T112141Z` has no red window at the default scale;
  `./serve.sh local --llm-provider mock --sg-moderate-from 10.0001` makes its 11:25 window red.
- Without the VM: `./rejudge.sh <id>` (prints the LLM input, no call) or `./rejudge.sh <id> anthropic`.
- An LLM "FP" is not a label and is never used for training.

A red window is an observation, not a detection result, until the window has labels (DEVIATIONS D6, D8).

## Outputs per session (`src/live/sessions/<id>/`)
- `session.json`: model, scaler, converter sha256, dos_map, parameters.
- `raw/<stream>.json`: verbatim; can be replayed or converted into a labelled test split.
- `windows.csv`: one row per window, incl. `subgraphs_kept, max_sg_score, max_level, alert, llm`
  (`llm` = pending when written; final verdicts in `llm_verdicts.csv`).
- `windows/<wid>/`: `edges.csv, node_attrs.csv, features_model.csv, scores.csv, subgraphs.json`, and
  `llm/` (`sg<id>_doc.txt` = exact LLM input, `triage.json`, `sg<id>_report.md`, `iocs.json`, `calls.jsonl`).
