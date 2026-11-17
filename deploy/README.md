# Public demo: OCR-APT live detector, recorded snapshot

`https://projects.datrollout.dev/nt2102/`: a read-only, explorable copy of what the live detector showed from
**2026-11-02 23:10Z to 2026-11-15 20:15Z**. That range runs up to the last real attack that got an LLM `attack` verdict
(teammate on `ws-etw-1`, window 19:55Z) plus the three windows after it. Employers can open any window, see the
anomalous subgraph, the top nodes per type and the LLM report, while the live panels replay the attack session
(2026-11-15 15:10Z → 20:15Z) at one 5-min window every 3 s, in a loop.

**What it is and is not.** Every score, alert level and LLM verdict shown is the one the live server computed at the
time, with the model and settings recorded in that session's `session.json`. Nothing is re-scored in the container:
there is no model, no torch, no Loki and no LLM key. The raw ETW logs of 11-15 afternoon are gone (12 h retention), so
re-scoring would not be possible anyway. The 4.5 alert cut-off and the NET rule being off (since 2026-11-15) were tuned
on the 2026-11-15 react2shell run, so that run is in-sample evidence, not an independent test (docs/DEVIATIONS.md D8).
The UI banner says the same.

```
trainer: src/live/sessions/ (3.2 GB, raw logs included)
   │  lab snapshot build --until 2026-11-15T20:15Z      (src/live/snapshot.py: winners only, redacted, sha256 manifest)
   ▼
deploy/snapshot/nt2102-snapshot.tar.gz   5.6 MB, 4,658 windows, 4,583 details, 63 red, 17 LLM "attack"
   │  .github/workflows/demo-image.yml   (selftests → buildx → ghcr.io/uitprojects/nt2102-demo:X.Y.Z → smoke test)
   ▼
image 239 MB (python:3.12-slim + fastapi/uvicorn/pandas/networkx; no torch)
   │  Argo CD (home-lab app-of-app → this repo's deploy/k8s, manual sync)
   ▼
pod: unpack + verify into an emptyDir (~1 s) → live_server.py --snapshot → Traefik (crowdsec, path strip) → visitors
```

## The persistence layer (why the same server can serve live data and a snapshot)

The live server's storage is behind three abstract repositories in `src/live/repository.py`. They play the role of
Spring Data repository interfaces: the engine, the cross-host correlator and the HTTP layer only know the interface.

| Port (ABC) | What it stores | Live implementation | Snapshot implementation |
|---|---|---|---|
| `HistoryRepository` | every window state, queryable by time range; the UI detail of a window | `history.FileHistoryRepository` (`history.jsonl` + `detail.json`) | `snapshot.SnapshotHistoryRepository` (read-only) |
| `CrosshostRepository` | cross-host findings | `file_store.FileCrosshostRepository` (`crosshost.jsonl`) | `snapshot.SnapshotCrosshostRepository` (read-only) |
| `SessionStore` | the engine's own files: `session.json`, raw lines, per-window CSV/JSON, `windows.csv`, LLM verdicts, daily LLM budget | `file_store.FileSessionStore` (`sessions/` folder) | `snapshot.SnapshotSessionStore`: reads work, writes raise `ReadOnlyRepositoryError` |

`repository.open_repositories("files" | "snapshot", ...)` is the only place that picks an implementation. Shared
logic lives in the abstract base: the History query (dedup of restart copies, alerts-only, per-host), the detail merge
and the path-traversal guard. This is a template-method base class, like a Java abstract class with concrete methods.
In live mode `live_server.py` wires the `files` implementations into `Engine` and `Correlator`. The bytes on disk
are unchanged by the refactor, and `engine.py` / `correlate.py` selftests still pass. In snapshot mode
`playback.SnapshotMember` stands in for `Engine` behind the same HTTP routes, so the UI is the same file.

## Run it locally

```bash
# straight from the repo (no Docker): needs only the torch-free deps
uv run python src/live/live_server.py --snapshot deploy/snapshot/nt2102-snapshot.tar.gz --data-dir /tmp/nt2102-demo \
    --port 8000 --playback-from 2026-11-15T15:10:00Z
# or the image, with the same hardening as in the cluster
docker build -f deploy/docker/Dockerfile -t nt2102-demo:local .
docker run --rm -p 8000:8000 --read-only --tmpfs /data --tmpfs /tmp --memory 256m --cap-drop ALL nt2102-demo:local
```

## Rebuild the snapshot

On the trainer, after `main` with this code is deployed there (or from a scratch copy of `src/live/{repository,history,
file_store,snapshot}.py` + `src/pipeline/{alg2,cmdlines}.py`, which is how the committed one was built on 2026-11-16):

```bash
source .venv/bin/activate
lab snapshot build --until 2026-11-15T20:15Z --out /tmp/nt2102-snapshot.tar.gz
```

- **Which windows are kept:** the History winner per host and window start. Restart duplicates (`.rN`) and replay
  sessions are left out.
- **What is copied per window:** `detail.json`, rebuilt from `scores.csv` where it is missing, plus `session.json`
  with absolute paths cut to names. Raw lines, edges, features and LLM prompt logs (`calls.jsonl`) stay behind.
- **Redaction:** the passwords in `src/ansible/proxmox.ini`, `$LAB_TOKEN`, and password-looking arguments in
  command lines. The output is then re-scanned, and the build fails if a known secret is still in it.
- **Deterministic:** the same input gives the same files and hashes.

Copy the archive to `deploy/snapshot/` and release a new image tag.

## Release and deploy

1. Merge to `main`. CI runs the snapshot-path selftests, builds and pushes `:sha-<short>` and `:edge`, then smoke-tests
   the image (`/healthz`, snapshot counts, `POST /ingest` → 403).
2. `git tag v1.0.0 && git push origin v1.0.0` builds `ghcr.io/uitprojects/nt2102-demo:1.0.0`.
   **Once:** make the package public in the UITProjects org (Packages → nt2102-demo → settings), because the
   homelab pulls without a secret.
3. Argo CD Application (kept in this repo, no home-lab change): `kubectl apply -f deploy/argocd/nt2102-demo.yaml`.
   No DNS change: `projects.datrollout.dev` already resolves to the DDNS host (checked 2026-11-17), shared by all project
   demos (see "Path routing"). The uncommitted home-lab drafts (app-of-app template `nt2102-demo.yaml`, DNS record `nt2102`)
   are not needed; keep only one Application definition.
4. Argo CD: sync `nt2102-demo` (manual sync, the homelab convention; `CreateNamespace` makes `nt2102-demo`).
5. Check:
   ```bash
   kubectl -n nt2102-demo get pods
   curl -sI https://projects.datrollout.dev/nt2102            # 308, Location: …/nt2102/
   curl -sI https://projects.datrollout.dev/nt2102/healthz
   curl -sI https://projects.datrollout.dev/  # 404 (no landing page yet)
   python deploy/loadtest.py --url https://projects.datrollout.dev/nt2102 --users 10 --duration 300
   ```

New code or a new snapshot: release `v1.0.1`, bump the tag in `deploy/k8s/deployment.yaml`, sync.

## Kubernetes objects (`deploy/k8s/`, plain YAML in the homelab style)

| File | What |
|---|---|
| `deployment.yaml` | 1 replica; non-root uid 10001, read-only root FS, all capabilities dropped, seccomp RuntimeDefault, no SA token; emptyDir `/data` (unpacked snapshot) + `/tmp`; startup/readiness/liveness on `/healthz` |
| `service.yaml` | ClusterIP :8000 (`http`) |
| `middleware-path.yaml` | Traefik `RedirectRegex` `slash` (`/nt2102` → `/nt2102/`) + `StripPrefix` `strip` (`/nt2102`) |
| `ingress.yaml` | `projects.datrollout.dev`, path `/nt2102`, websecure + `letsencrypt` resolver, middlewares `traefik-crowdsec` → slash → strip; no rate limit (user, 2026-11-17); no VN geo filter (employers abroad) |

All four (deployment, service, middleware-path, ingress) pass `kubectl apply --dry-run=server` on the homelab cluster (2026-11-17).

## Path routing

One path per project on the shared host `projects.datrollout.dev`. Traefik redirects `/nt2102` → `/nt2102/` and strips the
prefix, so the app serves at `/`. Keep UI URLs relative: `grep -nE '(fetch|EventSource)\(["`]/|(href|src)="/' src/live/static/index.html`
must print nothing. `/` has no router (404).

## Measured resources (local container, 2026-11-16)

Load from `deploy/loadtest.py`. Each virtual user keeps one SSE stream open and loops: status → windows of both hosts →
History 6 h → History 7 d alerts-only → 3 window details, with 2–5 s think time. This is far busier than a real visitor.
Container: `--read-only --memory 256m --cap-drop ALL`, snapshot unpacked to tmpfs.

| Load | Throughput | p95 latency (worst endpoint) | CPU peak | Process RSS | Errors |
|---|---|---|---|---|---|
| idle (playback only) | – | – | ~1 % | 132 MiB | – |
| 10 users, 120 s | 22 req/s | 50 ms (History 7 d) | 0.35 core | 133 MiB | 0 |
| 30 users, 60 s | 61 req/s | 56 ms (History 7 d) | 0.57 core | 134 MiB | 0 |

Hence `requests: cpu 50m, memory 160Mi` and `limits: cpu 1, memory 256Mi`. Memory is flat under load, because
the whole history (~5k rows) is loaded once at start. The headroom covers the unpack at start. The cluster numbers will
come from `kubectl top pod -n nt2102-demo` after the first sync. Re-run the load test against the public URL then and update this
table.

## Tests

`./selftest.sh` runs every module's `--selftest`, including the new `repository`, `file_store`, `snapshot` and
`playback` ones and the snapshot-mode HTTP test in `live_server.py`. They prove the code runs (cut-off, dedup,
redaction, tamper and traversal refused, read-only writes refused, playback over HTTP, no torch import). They say
nothing about how well the detector detects attacks.
