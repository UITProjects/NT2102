# deploy/: public demo

A read-only copy of the live detector at **https://projects.datrollout.dev/nt2102/**, so visitors can browse
every recorded window, its anomalous subgraph, top nodes and LLM report. The live panels replay the attack
session of 2026-11-15 15:10Z → 20:15Z, one 5-min window every 3 s.

**It is a replay.** Every score, alert level and LLM verdict is the one computed live at the time, with that
session's model and settings. The container has no model, no torch, no Loki and no LLM key. The 4.5 alert cut-off
was tuned on the react2shell run it shows (in-sample, `docs/DEVIATIONS.md` D8). The UI banner says the same.

## Outcome

- Snapshot: 2026-11-02 23:10Z → 2026-11-15 20:15Z, 4,658 windows, 63 red, 17 LLM `attack`; 5.6 MB, redacted, sha256 per file.
- Image: 239 MB, `python:3.12-slim`, hash-pinned deps, non-root, read-only root FS.
- Load test (local, 30 users): 61 req/s, 0 errors, p95 ≤ 56 ms, 0.57 core, RSS 134 MiB flat.
  Sized at requests 50m / 160Mi, limits 1 CPU / 256Mi.

## Architecture

```
live sessions (trainer) ─► snapshot builder: one result per window, redacted, deterministic tar.gz
   ─► CI: selftests → build → ghcr.io/uitprojects/nt2102-demo:X.Y.Z → smoke test
   ─► Argo CD (manual sync) ─► pod: unpack + verify sha256 into emptyDir ─► live_server.py --snapshot
   ─► Traefik: crowdsec, /nt2102 → /nt2102/, strip prefix ─► visitors
```

Persistence sits behind three repository interfaces (`src/live/repository.py`: history, cross-host findings,
session files). Live mode uses the file implementations and the demo uses the read-only snapshot ones, so
the HTTP routes and UI are identical in both modes.

## Key files

| path | what |
|---|---|
| `snapshot/nt2102-snapshot.tar.gz` | recorded results + `manifest.json` |
| `docker/Dockerfile`, `docker/requirements.txt` | demo image (torch-free) |
| `k8s/` | Deployment, Service, Ingress, Traefik path middlewares |
| `argocd/nt2102-demo.yaml` | Argo CD Application |
| `loadtest.py` | SSE + History load test |
| `../.github/workflows/demo-image.yml` | build, push and smoke-test pipeline |
