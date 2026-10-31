#!/usr/bin/env python3
"""
live_server.py - HTTP layer around engine.Engine: ingest from Loki or the shipper, JSON API + SSE for the UI.

    --loki-url URL            follow the newest test run in Loki (Alloy on the VM ships to it; loki_source.py)
    POST /ingest              shipper/replay batches (JSON, optionally gzip). 409 = offset gap, body.expected
    GET  /api/status          shipper / session / model status
    GET  /api/windows         every window of the current session (summary rows)
    GET  /api/windows/{wid}   one window: top nodes per type, Alg. 1 subgraphs (score, level, kept),
                              LLM verdict + attack report when --llm-provider is set (alg2.py)
    GET  /api/stream          Server-Sent Events: "window" (a window changed), "status" (after 2 s idle)
    GET  /                    the UI (static/index.html)

Usage (from the repo root): ./serve.sh [live|local] [flags] wraps
    uv run python src/live/live_server.py --bind 127.0.0.1 [--loki-url http://127.0.0.1:3100]
  The model comes from the current-model pointer (src/artifacts/current.local.json, else current.json;
  `lab promote <run>` sets it). --run-dir / --scaler / --experiment override it, e.g.
    --run-dir src/artifacts/runs/exp01 --scaler src/artifacts/features/self-dataset/feature_scaler.json
    add --llm-provider anthropic|openai|ollama|deepseek|mock for the LLM verdict on red windows (src/live/README.md)
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))          # src/: the `lab` package (current-model pointer)
from engine import Engine  # noqa: E402
import alg2  # noqa: E402  (engine put src/pipeline on sys.path; provider list + reasoning efforts)


def create_app(engine: Engine, token: str | None, source=None) -> FastAPI:
    app = FastAPI(title="OCR-APT live")
    clients: set[tuple] = set()                  # (event loop, asyncio.Queue) per open SSE connection

    def fan_out(ev):                             # called from the ingest or scoring thread
        for loop, q in list(clients):
            loop.call_soon_threadsafe(q.put_nowait, ev)
    engine.listeners.append(fan_out)

    @app.post("/ingest")
    async def ingest(request: Request):
        if token and request.headers.get("x-lab-token") != token:
            raise HTTPException(401, "bad token")
        body = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        payload = json.loads(body)
        code, resp = await run_in_threadpool(engine.ingest, payload)   # keep the event loop free
        return JSONResponse(resp, status_code=code)

    @app.get("/api/status")
    async def status():
        st = engine.status()
        if source is not None:
            st["source"] = source.status()
        return st

    @app.get("/api/windows")
    async def windows():
        return engine.windows()

    @app.get("/api/windows/{wid}")
    async def window(wid: str):
        d = engine.window_detail(wid)
        if d is None:
            raise HTTPException(404, "unknown window")
        return d

    @app.get("/api/stream")
    async def stream(request: Request):
        q: asyncio.Queue = asyncio.Queue()
        client = (asyncio.get_running_loop(), q)
        clients.add(client)

        async def gen():
            try:
                yield f"event: status\ndata: {json.dumps(engine.status(), default=str)}\n\n"
                while not await request.is_disconnected():
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=2.0)
                        yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield f"event: status\ndata: {json.dumps(engine.status(), default=str)}\n\n"
            finally:
                clients.discard(client)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(HERE, "static", "index.html"))

    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", help="train_gnn_models.py output (models/, train_meta.json); default: current model")
    ap.add_argument("--scaler", help="TRAIN feature_scaler.json; default: current model's")
    ap.add_argument("--experiment", help="training experiment.json (converter sha256 check); default: current model's")
    ap.add_argument("--bind", default="10.21.2.1", help="host-only IP the VM can reach (127.0.0.1 for replay)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--window-min", type=float, default=5, help="MUST equal the training window length")
    ap.add_argument("--stride-min", type=float, default=None, help="default = window (tumbling)")
    ap.add_argument("--lateness-s", type=float, default=20,
                    help="wait this long (VM clock) for stragglers before releasing events")
    ap.add_argument("--origin-ms", type=int, default=0,
                    help="window grid origin; 0 = aligned to the clock (10:00, 10:05, ...)")
    ap.add_argument("--sessions-dir", default=os.path.join(HERE, "sessions"))
    ap.add_argument("--token", default=os.environ.get("LAB_TOKEN"), help="optional shared secret (X-Lab-Token)")
    ap.add_argument("--allow-collapsed", action="store_true")
    g = ap.add_argument_group("Loki source (src/live/loki_source.py); without --loki-url only POST /ingest feeds the engine")
    g.add_argument("--loki-url", default=None, help="e.g. http://127.0.0.1:3100 (Loki on this host)")
    g.add_argument("--loki-split", default="test", choices=["train", "test"], help="follow the newest run of this split")
    g.add_argument("--loki-poll-s", type=float, default=1.0)
    g.add_argument("--loki-lookback-s", type=float, default=3600,
                   help="how far back to look for the newest run (raise it to follow an old replayed run)")
    g.add_argument("--loki-idle-s", type=float, default=None,
                   help="no new line for this long -> the clock follows host time (default 2 x lateness)")
    g = ap.add_argument_group("Alg. 1 / SG-Det (src/pipeline/alg1.py)")
    g.add_argument("--n-seed", type=int, default=15, help="[PAPER] seed nodes per node type")
    g.add_argument("--min-nodes", type=int, default=3, help="[AUTHOR CODE] smallest subgraph kept by construction")
    g.add_argument("--max-edges", type=int, default=5000, help="[PAPER] max_e; larger subgraphs are split (0 = no cap)")
    g.add_argument("--sg-min-level", default="Moderate", choices=["Negligible", "Minor", "Moderate", "Significant", "Critical"],
                   help="window alerts only if a subgraph reaches this level (Moderate = score >= --sg-moderate-from)")
    g.add_argument("--sg-moderate-from", type=float, default=30.0,
                   help="[OURS] Moderate starts at this subgraph score (default 30; [AUTHOR CODE] scale: 10.0001)")
    g.add_argument("--sg-min-score", type=float, default=None,
                   help="[OURS] numeric cut-off instead of the level (alert if sg_score > X); calibrate on BENIGN sessions")
    g = ap.add_argument_group("LLM triage + attack report after SG-Det (src/pipeline/alg2.py)")
    g.add_argument("--llm-provider", choices=list(alg2.DEFAULT_MODELS), default=None,
                   help="off by default; only red windows are sent; the verdict never turns a red window green")
    g.add_argument("--llm-model", default=None, help="default per provider, see alg2.DEFAULT_MODELS")
    g.add_argument("--llm-base-url", default=None,
                   help="OpenAI-compatible endpoint, e.g. http://host:8000/v1 (or a remote Ollama :11434/v1)")
    g.add_argument("--llm-api-key", default=None,
                   help="key for an OpenAI-compatible --llm-base-url (else $OPENAI_API_KEY; keyless endpoints use a "
                        "dummy). deepseek: else $NT2102_DEEPSEEK_API_KEY, else ~/.config/nt2102/credentials.json, "
                        "else a prompt that saves it there")
    g.add_argument("--llm-reasoning-effort", choices=alg2.REASONING_EFFORTS, default=None,
                   help="deepseek: thinking effort; none = thinking off (fastest, use for the live demo); "
                        "default: not sent = DeepSeek default (thinking, high)")
    g.add_argument("--llm-debug", action="store_true", default=alg2.debug_default(),
                   help="print each LLM call's raw response / HTTP error body to stderr, keep it in calls.jsonl "
                        "(default from $NT2102_LLM_DEBUG=1)")
    g.add_argument("--llm-max-subgraphs", type=int, default=5, help="kept subgraphs investigated per window")
    a = ap.parse_args()
    if not (a.run_dir and a.scaler):
        from lab import current
        cur = current()
        if not cur:
            ap.error("no --run-dir/--scaler and no current-model pointer (src/artifacts/current.json)")
        explicit = a.run_dir is not None
        a.run_dir = a.run_dir or cur["run_dir"]
        a.scaler = a.scaler or cur["scaler"]
        if not explicit and a.experiment is None and cur.get("experiment") and os.path.exists(cur["experiment"]):
            a.experiment = cur["experiment"]
        print(f"[live] current model {cur['run']} ({os.path.relpath(cur['pointer'])})")
    if not 1 < a.sg_moderate_from <= 100:
        ap.error("--sg-moderate-from must be in (1, 100]")

    if a.window_min != 5:
        print(f"[live] WARNING window {a.window_min} min: the models were trained on 5-min windows "
              f"(see experiment.json windowing.window_min); features will be off-distribution")
    engine = Engine(a.run_dir, a.scaler, a.sessions_dir, a.window_min, a.stride_min, a.lateness_s,
                    a.origin_ms, experiment_json=a.experiment, allow_collapsed=a.allow_collapsed,
                    n_seed=a.n_seed, min_nodes=a.min_nodes, max_edges=a.max_edges,
                    sg_min_level=a.sg_min_level, sg_min_score=a.sg_min_score,
                    sg_moderate_from=a.sg_moderate_from, llm_provider=a.llm_provider,
                    llm_model=a.llm_model, llm_base_url=a.llm_base_url, llm_api_key=a.llm_api_key,
                    llm_max_subgraphs=a.llm_max_subgraphs, llm_reasoning_effort=a.llm_reasoning_effort,
                    llm_debug=a.llm_debug)
    print(f"[live] model {engine.run_dir}  tau={engine.run.tau}")
    print(f"[live] Alg. 1 {engine.sg}  train score range {engine.run.score_range}")
    print(f"[live] LLM {engine.llm_cfg}")
    print(f"[live] converter sha256 {engine.conv_sha} ({engine.conv_check})")
    source = None
    if a.loki_url:
        from loki_source import LokiSource
        source = LokiSource(engine, a.loki_url, split=a.loki_split, poll_s=a.loki_poll_s, lookback_s=a.loki_lookback_s,
                            idle_s=a.loki_idle_s if a.loki_idle_s is not None else 2 * a.lateness_s).start()
        print(f"[live] following Loki {a.loki_url} split={a.loki_split}")
    print(f"[live] UI: http://{a.bind}:{a.port}/")
    uvicorn.run(create_app(engine, a.token, source), host=a.bind, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
