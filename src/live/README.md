# src/live/: live detector

Follows each Windows VM's ETW stream in Loki, scores every 5-minute window with that host's OCRGCN model,
turns anomalous nodes into alert subgraphs, asks an LLM for a verdict on red windows, and serves it all in a
web UI with a persistent, range-queryable history. The same server replays a recorded snapshot for the public demo.

## Outcome

- Ran 24/7 on the trainer for two weeks on two hosts, with one model per host.
- DVWA kill chain and the real react2shell attack both went red with an LLM `attack` verdict; idle windows stayed
  mostly green (a few benign false alarms a day).
- The live path reproduces the offline pipeline on the training capture (`compare_replay.py`).
- Self-healing: watchdog exit + systemd restart, `/healthz`, a restart resumes from Loki.

## Architecture

```
Loki ─► loki_source.py (one per scored VM) ─► engine.py
          reorder by event time → provenance graph (same converter as training) → 5-min windows
          → train scaler + relations → OCRGCN per node type → anomalous nodes
          → Alg. 1 subgraphs + SG-Det level (alg1.py) → red / green
          → LLM thread: triage verdict + Alg. 2 report (alg2.py, daily budget)
     ─► correlate.py: red window's network nodes → peer VM's process at the other end → both sides to the LLM
     ─► repositories (history, cross-host findings, session files) ─► live_server.py (FastAPI + SSE) ─► UI
```

Alert rule: subgraph score = Σ clamped min-max node probability; red at ≥ Moderate (paper scale 10, live 4.5 [OURS], D8).
The LLM verdict is an overlay, never a label or training data (D9). Command lines from Sysmon are shown to
the UI and the LLM only, never used as features (D14).

## Key files

| file | what |
|---|---|
| `engine.py` | ingest, windows, scoring thread, LLM thread |
| `loki_source.py` · `loki_push.py` · `etwship.py` | Loki input · recorded-run feeder · HTTP replay shipper |
| `correlate.py` | cross-host follow-up (D13): `pending` / `one-sided` / `corroborated` |
| `live_server.py` · `static/index.html` | HTTP API, SSE stream, UI |
| `repository.py` · `history.py` · `file_store.py` | persistence interfaces and file implementations |
| `snapshot.py` · `playback.py` | snapshot build and read-only replay (public demo) |
| `compare_replay.py` | live vs offline equality check |
