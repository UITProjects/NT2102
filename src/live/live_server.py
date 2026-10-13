#!/usr/bin/env python3
"""
live_server.py - HTTP layer around engine.Engine: ingest from the VM shipper, JSON API + SSE for the UI.

    POST /ingest              shipper batches (JSON, optionally gzip). 409 = offset gap, body.expected
    GET  /api/status          shipper / session / model status
    GET  /api/windows         every window of the current session (summary rows)
    GET  /api/windows/{wid}   one window: top nodes per type, Alg. 1 subgraphs (score, level, kept),
                              LLM verdict + attack report when --llm-provider is set (alg2.py)
    GET  /api/stream          Server-Sent Events: "window" (a window changed), "status" (every 2 s)
    GET  /                    the UI (static/index.html)

Usage (from the repo root)
    uv run python src/live/live_server.py --run-dir src/artifacts/runs/exp01 \
        --scaler src/artifacts/features/self-dataset/feature_scaler.json \
        --experiment src/out/etw/experiments/20261009T145632Z/experiment.json --bind 10.21.2.1
    add  --llm-provider anthropic|openai|ollama|mock  for the LLM verdict on red windows (uv sync --group llm;
    'mock' is a keyword rule for UI tests, not an LLM)
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
from engine import Engine  # noqa: E402


def create_app(engine: Engine, token: str | None) -> FastAPI:
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
        return engine.status()

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
    ap.add_argument("--run-dir", required=True, help="train_gnn_models.py output (models/, train_meta.json)")
    ap.add_argument("--scaler", required=True, help="TRAIN feature_scaler.json")
    ap.add_argument("--experiment", help="training experiment.json: checks the converter sha256 matches")
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
    g = ap.add_argument_group("Alg. 1 / SG-Det (src/pipeline/alg1.py)")
    g.add_argument("--n-seed", type=int, default=15, help="[PAPER] seed nodes per node type")
    g.add_argument("--min-nodes", type=int, default=3, help="[REPO] smallest subgraph kept by construction")
    g.add_argument("--max-edges", type=int, default=5000, help="[PAPER] max_e; larger subgraphs are split (0 = no cap)")
    g.add_argument("--sg-min-level", default="Moderate", choices=["Negligible", "Minor", "Moderate", "Significant", "Critical"],
                   help="window alerts only if a subgraph reaches this level (Moderate = score >= --sg-moderate-from)")
    g.add_argument("--sg-moderate-from", type=float, default=30.0,
                   help="[OURS] Moderate starts at this subgraph score (default 30; [REPO] scale: 10.0001)")
    g.add_argument("--sg-min-score", type=float, default=None,
                   help="[OURS] numeric cut-off instead of the level (alert if sg_score > X); calibrate on BENIGN sessions")
    g = ap.add_argument_group("LLM triage + attack report after SG-Det (src/pipeline/alg2.py)")
    g.add_argument("--llm-provider", choices=["anthropic", "openai", "ollama", "mock"], default=None,
                   help="off by default; only red windows are sent; the verdict never turns a red window green")
    g.add_argument("--llm-model", default=None, help="default per provider, see alg2.DEFAULT_MODELS")
    g.add_argument("--llm-base-url", default=None, help="e.g. a remote Ollama http://host:11434/v1")
    g.add_argument("--llm-max-subgraphs", type=int, default=5, help="kept subgraphs investigated per window")
    a = ap.parse_args()
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
                    llm_model=a.llm_model, llm_base_url=a.llm_base_url, llm_max_subgraphs=a.llm_max_subgraphs)
    print(f"[live] model {engine.run_dir}  tau={engine.run.tau}")
    print(f"[live] Alg. 1 {engine.sg}  train score range {engine.run.score_range}")
    print(f"[live] LLM {engine.llm_cfg}")
    print(f"[live] converter sha256 {engine.conv_sha} ({engine.conv_check})")
    print(f"[live] UI: http://{a.bind}:{a.port}/")
    uvicorn.run(create_app(engine, a.token), host=a.bind, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
