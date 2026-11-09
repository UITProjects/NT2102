#!/usr/bin/env python3
"""
live_server.py - HTTP layer around engine.Engine: ingest from Loki or the shipper, JSON API + SSE for the UI.

    --loki-url URL            follow the newest test run in Loki (Alloy on the VM ships to it; loki_source.py)
    POST /ingest              shipper/replay batches (JSON, optionally gzip). 409 = offset gap, body.expected
    GET  /api/status          shipper / session / model status; ?host= picks a VM (default: the first)
    GET  /api/windows         every window of the current session (summary rows); ?host=
    GET  /api/windows/{wid}   one window: top nodes per type, Alg. 1 subgraphs (score, level, kept),
                              LLM verdict + attack report when --llm-provider is set (alg2.py); ?host=
    GET  /api/campaigns       cross-host links and campaigns (correlate.py), newest first; /api/campaigns/{id}
    GET  /api/stream          Server-Sent Events: "window", "campaign", "status" (after 2 s idle); ?host=
    GET  /healthz             200 {"ok": true} | 503 {"ok": false, "reason"}: engine threads alive and (with
                              --loki-url) a successful Loki poll within 120 s. For monitoring / curl checks.
    GET  /                    the UI (static/index.html)

Usage (from the repo root): ./serve.sh [live|local] [flags] wraps
    uv run python src/live/live_server.py --bind 127.0.0.1 [--loki-url http://127.0.0.1:3100]
    uv run python src/live/live_server.py --selftest      (/healthz + watchdog logic; no model, no Loki)
  The model comes from the current-model pointer (src/artifacts/current.local.json, else current.json;
  `lab promote <run>` sets it). With per-host models (`lab promote <run> --host <vm>`, `lab hosts <vm> ...`) one engine runs
  per active VM, each with its own model; with >= 2 VMs and /etc/nt2102-hosts.json, correlate.py links them.
  --run-dir / --scaler / --experiment override it, e.g.
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
import threading
import time

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))          # src/: the `lab` package (current-model pointer)
from engine import Engine  # noqa: E402
from history import HistoryStore, parse_time_ms  # noqa: E402
import alg1  # noqa: E402  (engine put src/pipeline on sys.path; default level scale)
import alg2  # noqa: E402  (provider list + reasoning efforts)


def health(engine, source=None, max_age_s=120.0):
    """(ok, reason) for /healthz: engine threads alive, and the Loki source polled successfully lately."""
    ok, why = engine.health()
    if ok and source is not None:
        ok, why = source.health(max_age_s)
    return ok, why


def watchdog(engine, source, after_s, every_s=15.0, exit_fn=os._exit, sleep=time.sleep, now=time.monotonic):
    """24/7 guard: exit (systemd Restart=always starts a fresh process) when a worker thread died or the Loki
    source hangs for after_s. NOT on Loki merely being down: a restart cannot fix Loki and the source retries."""
    bad_since = None
    while True:
        ok, why = engine.health()
        why = None if ok else why
        if why is None and source is not None:
            why = source.stuck(after_s)
        t = now()
        if why is None:
            bad_since = None
        elif bad_since is None:
            bad_since = t
            print(f"[watchdog] unhealthy: {why}; exiting in {after_s:.0f}s unless it recovers", flush=True)
        elif t - bad_since >= after_s:
            print(f"[watchdog] still unhealthy ({why}); exiting so systemd restarts the server", flush=True)
            exit_fn(3)
            return
        sleep(every_s)


def trained_text(pairs: list) -> list:
    """[(model, [hosts] or None)] -> what the UI says each model was trained for. Never a guess: a model whose hosts are
    not recorded says so. Models with the same hosts-list stay separate lines, in order."""
    out = []
    for model, hosts in pairs:
        text = f"{model} is trained for {' and '.join(hosts)}" if hosts else f"{model}: trained host not recorded"
        out.append({"model": model, "hosts": hosts or [], "text": text})
    return out


def create_app(engine: Engine, token: str | None, source=None, members=None, correlator=None, trained=None) -> FastAPI:
    """members = [{"host", "engine", "source"}], one per scored VM; without it, one member from engine + source."""
    app = FastAPI(title="OCR-APT live")
    clients: set[tuple] = set()                  # (event loop, asyncio.Queue) per open SSE connection
    members = members or [{"host": None, "engine": engine, "source": source}]

    def fan_out(ev):                             # called from the ingest or scoring thread
        for loop, q in list(clients):
            loop.call_soon_threadsafe(q.put_nowait, ev)
    for m in members:
        m["engine"].listeners.append(lambda ev, h=m["host"]: fan_out({**ev, "host": h}))
    if correlator is not None:
        correlator.listeners.append(fan_out)

    def pick(host):
        if host is None:
            return members[0]
        for m in members:
            if m["host"] == host:
                return m
        raise HTTPException(404, f"unknown host {host}")

    def summary(m):
        """What the UI needs for every VM at once; the full status is only sent for the selected one."""
        st = m["engine"].status()
        s = st.get("session") or {}
        return {"host": m["host"], "model": os.path.basename(st["model"]["run_dir"]), "session": s.get("id"),
                "last_seen_s_ago": s.get("last_seen_s_ago"), "open_window": s.get("open_window"),
                "watermark_ms": s.get("watermark_ms"), "windows": s.get("windows", 0)}

    def full_status(host=None):
        m = pick(host)
        st = m["engine"].status()
        if m["source"] is not None:
            st["source"] = m["source"].status()
        return {**st, "selected_host": m["host"], "hosts": [summary(x) for x in members], "trained_for": trained or [],
                "correlation": correlator.status() if correlator is not None else None}

    @app.post("/ingest")
    async def ingest(request: Request):
        if token and request.headers.get("x-lab-token") != token:
            raise HTTPException(401, "bad token")
        body = await request.body()
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        payload = json.loads(body)
        code, resp = await run_in_threadpool(members[0]["engine"].ingest, payload)   # keep the event loop free
        return JSONResponse(resp, status_code=code)

    @app.get("/api/status")
    async def status(host: str | None = None):
        return full_status(host)

    @app.get("/api/windows")
    async def windows(host: str | None = None):
        return pick(host)["engine"].windows()

    @app.get("/api/windows/{wid}")
    async def window(wid: str, host: str | None = None):
        d = pick(host)["engine"].window_detail(wid)
        if d is None:
            raise HTTPException(404, "unknown window")
        return d

    # ---- cross-host links and campaigns (correlate.py)
    @app.get("/api/campaigns")
    async def campaigns(limit: int = Query(100, ge=1, le=1000)):
        return correlator.list(limit) if correlator is not None else []

    @app.get("/api/campaigns/{cid}")
    async def campaign(cid: str):
        c = correlator.get(cid) if correlator is not None else None
        if c is None:
            raise HTTPException(404, "unknown campaign")
        return c

    # ---- persistent history (history.py): every window ever closed, across restarts and session rotations
    @app.get("/api/history")
    async def history(start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to"),
                      alert: bool = False, replay: bool = False, limit: int = Query(2000, ge=1, le=20000),
                      host: str | None = None):
        """Windows overlapping [from, to); from/to = ISO-8601 (naive = UTC) or epoch ms; default the last 24 h."""
        try:
            to_ms = parse_time_ms(end) or int(time.time() * 1000)
            from_ms = parse_time_ms(start) or to_ms - 24 * 3600 * 1000
        except ValueError as e:
            raise HTTPException(400, f"bad time: {e}")
        if from_ms >= to_ms:
            raise HTTPException(400, "from must be before to")
        return await run_in_threadpool(members[0]["engine"].history.query, from_ms, to_ms, alert, replay, limit, host)

    @app.get("/api/history/{sdir}/{wid}")
    async def history_window(sdir: str, wid: str):
        for m in members:
            S = m["engine"].session
            if S is not None and os.path.basename(S["dir"]) == sdir:   # current session: live memory (LLM pending etc.)
                d = m["engine"].window_detail(wid)
                if d is not None:
                    return {**d, "history": {"sdir": sdir, "session": S["id"], "mode": S["mode"]}}
        try:
            d = await run_in_threadpool(members[0]["engine"].history.detail, sdir, wid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if d is None:
            raise HTTPException(404, "unknown window")
        return d

    @app.get("/api/stream")
    async def stream(request: Request, host: str | None = None):
        pick(host)                               # an unknown host is a 404 now, not a broken stream later
        q: asyncio.Queue = asyncio.Queue()
        client = (asyncio.get_running_loop(), q)
        clients.add(client)

        async def gen():
            try:
                yield f"event: status\ndata: {json.dumps(full_status(host), default=str)}\n\n"
                while not await request.is_disconnected():
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=2.0)
                        yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield f"event: status\ndata: {json.dumps(full_status(host), default=str)}\n\n"
            finally:
                clients.discard(client)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/healthz")
    async def healthz():
        for m in members:
            ok, why = health(m["engine"], m["source"])
            if not ok:
                return JSONResponse({"ok": False, "host": m["host"], "reason": why}, status_code=503)
        return JSONResponse({"ok": True, "reason": "ok"}, status_code=200)

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(HERE, "static", "index.html"))

    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", help="train_gnn_models.py output (models/, train_meta.json); default: current model")
    ap.add_argument("--scaler", help="TRAIN feature_scaler.json; default: current model's")
    ap.add_argument("--experiment", help="training experiment.json (converter sha256 check); default: current model's")
    ap.add_argument("--bind", default="127.0.0.1", help="bind address (0.0.0.0 to listen on all interfaces)")
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
    g.add_argument("--loki-host", action="append", metavar="VM",
                   help="score this Loki host, repeatable; overrides `lab hosts` (default: the active hosts of the pointer)")
    g.add_argument("--loki-default-host", metavar="VM",
                   help="follow this host when the pointer has no per-host models (without it: newest run of any host)")
    g.add_argument("--hosts-file", default=None, help="{host: ip} for cross-host correlation (default /etc/nt2102-hosts.json)")
    g.add_argument("--flow-baseline", default=None, help="known flows from `lab flows` (default src/artifacts/flow-baseline.json)")
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
    g.add_argument("--sg-moderate-from", type=float, default=alg1.MODERATE_FROM,
                   help="Moderate starts at this subgraph score (default 10 = [PAPER] §7.2.1; [AUTHOR CODE] > 10: "
                        "10.0001; old [OURS] live scale: 30)")
    g.add_argument("--sg-min-score", type=float, default=None,
                   help="[OURS] numeric cut-off instead of the level (alert if sg_score > X); calibrate on BENIGN sessions")
    g.add_argument("--sg-require-types", default=",".join(alg1.REQUIRE_TYPES), metavar="TYPES",
                   help="[OURS] alert only if a subgraph's anomalous nodes cover these types (default net: >= 1 anomalous "
                        "NET node; e.g. file,net,process); "
                        "a level-kept subgraph that misses one is logged as suppressed, stays normal and skips the LLM. "
                        "'none' = off (paper behaviour)")
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
    g.add_argument("--llm-max-per-day", type=int, default=10,
                   help="[OURS] at most this many red windows investigated per local day (token budget; each = 1 triage "
                        "request per kept subgraph, up to --llm-max-subgraphs, 3 if it is an attack, +9 for >= 2 attack "
                        "subgraphs); later ones show 'skipped'. 0 = no limit. "
                        "Count kept in <sessions-dir>/llm_budget.json, so restarts do not reset it")
    ap.add_argument("--watchdog-s", type=float, default=300,
                    help="exit (for systemd to restart) when a worker thread died or the Loki poll hangs this long; 0 = off")
    a = ap.parse_args()
    from lab import current, live_models, trained_hosts
    cur = current()
    explicit = a.run_dir is not None
    if not (a.run_dir and a.scaler) and not cur:
        ap.error("no --run-dir/--scaler and no current-model pointer (src/artifacts/current.json)")
    single = {"run": os.path.basename(os.path.normpath(a.run_dir)) if explicit else cur["run"],
              "run_dir": a.run_dir or cur["run_dir"], "scaler": a.scaler or cur["scaler"],
              "experiment": a.experiment or (None if explicit else cur.get("experiment"))}
    if single["experiment"] and not os.path.exists(single["experiment"]):
        single["experiment"] = None
    plans = plan_hosts(a.loki_host, a.loki_default_host, None if explicit else live_models(), single,
                       {} if explicit else cur.get("models") or {})
    if isinstance(plans, str):
        ap.error(plans)
    if not explicit and cur:
        print(f"[live] pointer {os.path.relpath(cur['pointer'])}: " + ", ".join(f"{p['host'] or 'any'}={p['run']}" for p in plans))
    if not 1 < a.sg_moderate_from <= 100:
        ap.error("--sg-moderate-from must be in (1, 100]")
    try:
        a.sg_require_types = alg1.parse_require_types(a.sg_require_types)
    except ValueError as e:
        ap.error(f"--sg-require-types: {e}")

    if a.window_min != 5:
        print(f"[live] WARNING window {a.window_min} min: the models were trained on 5-min windows "
              f"(see experiment.json windowing.window_min); features will be off-distribution")
    import correlate
    from history import HistoryStore as Store
    hosts_map = correlate.load_hosts(a.hosts_file or correlate.HOSTS_FILE)
    names = [p["host"] for p in plans]
    cor = None
    if len(plans) > 1:
        if all(h in hosts_map for h in names):
            baseline = correlate.load_baseline(a.flow_baseline or correlate.BASELINE_FILE)
            cor = correlate.Correlator({h: hosts_map[h] for h in names}, a.sessions_dir, int(a.window_min * 60_000), baseline)
            print(f"[live] correlation on: {cor.ips}, baseline "
                  + (f"{len(baseline)} known flows" if baseline is not None else "MISSING: every flow is new (lab flows)"))
        else:
            print(f"[live] correlation OFF: {names} not all in the hosts file ({hosts_map})")
    shared = Store(a.sessions_dir)
    members = []
    for p in plans:
        engine = Engine(p["run_dir"], p["scaler"], a.sessions_dir, a.window_min, a.stride_min, a.lateness_s,
                        a.origin_ms, experiment_json=p["experiment"], allow_collapsed=a.allow_collapsed,
                        n_seed=a.n_seed, min_nodes=a.min_nodes, max_edges=a.max_edges,
                        sg_min_level=a.sg_min_level, sg_min_score=a.sg_min_score,
                        sg_moderate_from=a.sg_moderate_from, sg_require_types=a.sg_require_types,
                        llm_provider=a.llm_provider,
                        llm_model=a.llm_model, llm_base_url=a.llm_base_url, llm_api_key=a.llm_api_key,
                        llm_max_subgraphs=a.llm_max_subgraphs, llm_reasoning_effort=a.llm_reasoning_effort,
                        llm_debug=a.llm_debug, llm_max_per_day=a.llm_max_per_day, host=p["host"], history=shared,
                        flow_sink=cor.add_line if cor else None)
        print(f"[live] {p['host'] or 'any host'}: model {engine.run_dir}  tau={engine.run.tau}")
        print(f"[live] converter sha256 {engine.conv_sha} ({engine.conv_check})")
        if cor:
            def forward(ev, h=p["host"], e=engine):
                if ev["type"] == "window":
                    cor.on_window(h, ev["window"], os.path.basename(e.session["dir"]))
            engine.listeners.append(forward)
        members.append({"host": p["host"], "engine": engine, "source": None})
    first = members[0]["engine"]
    if cor:
        cor.llm, cor.take_budget = first.llm, first._take_budget
    print(f"[live] Alg. 1 {first.sg}")
    print(f"[live] LLM {first.llm_cfg}")
    if a.loki_url:
        from loki_source import LokiSource
        for p, m in zip(plans, members):
            follow = p["host"] or a.loki_default_host
            m["source"] = LokiSource(m["engine"], a.loki_url, split=a.loki_split, poll_s=a.loki_poll_s,
                                     lookback_s=a.loki_lookback_s, host=follow,
                                     idle_s=a.loki_idle_s if a.loki_idle_s is not None else 2 * a.lateness_s).start()
            print(f"[live] following Loki {a.loki_url} split={a.loki_split} host={follow or 'any'}")
    if a.watchdog_s > 0:
        for m in members:
            threading.Thread(target=watchdog, args=(m["engine"], m["source"], a.watchdog_s), daemon=True,
                             name=f"watchdog-{m['host']}").start()
    print(f"[live] UI: http://{a.bind}:{a.port}/   health: /healthz   watchdog {a.watchdog_s:.0f}s")
    # a host assignment in the pointer is the operator's statement; otherwise only what the model's own files record
    assigned = {} if explicit else cur.get("models") or {}
    trained = trained_text([(p["run"], [p["host"]] if assigned.get(p["host"]) == p["run"] else trained_hosts(p["run_dir"]))
                            for p in plans])
    uvicorn.run(create_app(first, a.token, members[0]["source"], members, cor, trained), host=a.bind, port=a.port,
                log_level="warning")


def plan_hosts(loki_hosts, default_host, pointer_models, single, assigned) -> list | str:
    """Which engines to start. -> [{"host", "run", "run_dir", "scaler", "experiment"}], or an error string.

    loki_hosts: --loki-host; pointer_models: lab.live_models(), None with an explicit --run-dir; single: the one top-level
    model; assigned: {host: model} from the pointer. host None = the old single engine, which behaves as before."""
    if loki_hosts:
        if len(set(loki_hosts)) != len(loki_hosts):
            return f"--loki-host repeated: {loki_hosts}"
        if pointer_models is None or not assigned:            # one model for all: an explicit model, or no assignments
            return [{"host": h, **single} for h in loki_hosts]
        missing = [h for h in loki_hosts if h not in assigned]
        if missing:
            return f"no model for {missing}: lab promote <model> --host {missing[0]}"
        from lab.cli import RUNS, REPO, pointer_for
        out = []
        for h in loki_hosts:
            rec = pointer_for(os.path.join(RUNS, assigned[h]))
            out.append({"host": h, "run": rec["run"],
                        **{k: os.path.normpath(os.path.join(REPO, rec[k])) for k in ("run_dir", "scaler", "experiment")}})
        return out
    if pointer_models:
        return pointer_models
    return [{"host": None, **single}]


def selftest():
    """/healthz codes and the watchdog decision with stand-in engine/source (no model, no Loki, no network)."""
    from fastapi.testclient import TestClient

    import tempfile
    tmp = tempfile.TemporaryDirectory()

    class Eng:
        listeners, ok, session = [], True, None
        history = HistoryStore(tmp.name)

        def window_detail(self, wid):
            return None

        def health(self):
            return (True, "ok") if self.ok else (False, "engine thread(s) died: scoring")

    class Src:
        healthy, stuck_why = True, None

        def health(self, max_age_s):
            return (True, "ok") if self.healthy else (False, "no successful Loki poll for 130s")

        def stuck(self, after_s):
            return self.stuck_why

        def status(self):
            return {}
    eng, src = Eng(), Src()
    c = TestClient(create_app(eng, None, src))
    assert c.get("/healthz").status_code == 200
    src.healthy = False                                   # Loki down: 503, but the watchdog must NOT exit
    r = c.get("/healthz")
    assert r.status_code == 503 and "Loki" in r.json()["reason"], r.text

    def run_wd(ticks):
        clock, exits = [0.0], []

        def sleep(s):
            clock[0] += s
            if clock[0] > ticks * 15:
                raise StopIteration
        try:
            watchdog(eng, src, 60, every_s=15, exit_fn=exits.append, sleep=sleep, now=lambda: clock[0])
        except StopIteration:
            pass
        return exits
    assert run_wd(10) == [], "exited while Loki was merely down"
    src.stuck_why = "no Loki poll attempted for 400s (hung?)"
    assert run_wd(10) == [3], "a hung source must end the process"
    src.stuck_why, eng.ok = None, False
    assert c.get("/healthz").status_code == 503 and run_wd(10) == [3], "a dead engine thread must end the process"
    # /api/history: range in ISO or ms, alerts only, bad input -> 400, unknown -> 404, traversal -> 400
    base = 1_793_704_800_000
    for k, (alert, score) in enumerate([(False, 2.0), (True, 12.0), (False, 0.0)]):
        eng.history.append({"sdir": "live-a", "session": "live-a", "mode": "live", "moderate_from": 10.0,
                            "window": f"w{k}", "start_ms": base + k * 300_000, "end_ms": base + (k + 1) * 300_000,
                            "status": "scored", "alert": alert, "max_sg_score": score})
    r = c.get("/api/history", params={"from": "2026-11-03T11:20:00Z", "to": str(base + 900_000)}).json()
    assert [w["window"] for w in r["windows"]] == ["w0", "w1", "w2"], r
    r = c.get("/api/history", params={"from": "2026-11-03T11:20:00Z", "to": "2026-11-03T11:35:00Z", "alert": 1}).json()
    assert [w["window"] for w in r["windows"]] == ["w1"], r
    assert c.get("/api/history", params={"from": "yesterday"}).status_code == 400
    assert c.get("/api/history", params={"from": str(base), "to": str(base)}).status_code == 400
    assert c.get("/api/history").status_code == 200                        # default: last 24 h
    d = c.get("/api/history/live-a/w1")
    assert d.status_code == 200 and d.json()["window"]["max_sg_score"] == 12.0, d.text
    assert c.get("/api/history/live-a/nope").status_code == 404
    assert c.get("/api/history/.hidden/w1").status_code == 400
    # two VMs: ?host= reaches the right engine, an unknown one is a 404, the banner text, history by host, campaigns
    eng.ok, src.healthy = True, True

    class Eng2(Eng):
        listeners = []

        def __init__(self, run):
            self.run_dir = run

        def status(self):
            return {"host": None, "model": {"run_dir": self.run_dir}, "session": {"id": "s-" + self.run_dir}}

        def windows(self):
            return [{"window": "w-" + self.run_dir}]

    class Cor:
        listeners = []

        def status(self):
            return {"hosts": {}, "baseline": 3, "campaigns": {"link": 1, "suspected": 0, "confirmed": 0}}

        def list(self, limit):
            return [{"id": "c1"}]

        def get(self, cid):
            return {"id": cid} if cid == "c1" else None
    e0, e1 = Eng2("exp08"), Eng2("exp09-ws1")
    e0.listeners, e1.listeners = [], []
    trained = trained_text([("exp08", ["ws-etw-0"]), ("exp09-ws1", ["ws-etw-1"])])
    two = TestClient(create_app(e0, None, None, [{"host": "ws-etw-0", "engine": e0, "source": None},
                                                 {"host": "ws-etw-1", "engine": e1, "source": None}], Cor(), trained))
    st = two.get("/api/status", params={"host": "ws-etw-1"}).json()
    assert st["model"]["run_dir"] == "exp09-ws1" and st["selected_host"] == "ws-etw-1", st
    assert [h["model"] for h in st["hosts"]] == ["exp08", "exp09-ws1"], st["hosts"]
    assert [t["text"] for t in st["trained_for"]] == ["exp08 is trained for ws-etw-0", "exp09-ws1 is trained for ws-etw-1"]
    assert st["correlation"]["baseline"] == 3
    assert two.get("/api/status").json()["selected_host"] == "ws-etw-0"
    assert two.get("/api/windows", params={"host": "ws-etw-1"}).json() == [{"window": "w-exp09-ws1"}]
    assert two.get("/api/windows", params={"host": "nope"}).status_code == 404
    assert two.get("/api/stream", params={"host": "nope"}).status_code == 404
    assert two.get("/api/campaigns").json() == [{"id": "c1"}]
    assert two.get("/api/campaigns/c1").status_code == 200 and two.get("/api/campaigns/zz").status_code == 404
    assert c.get("/api/campaigns").json() == [] and c.get("/api/campaigns/c1").status_code == 404   # one VM: none
    assert trained_text([("m", None)])[0]["text"] == "m: trained host not recorded"
    assert trained_text([("m", ["a", "b"])])[0]["text"] == "m is trained for a and b"
    # which engines start
    one = {"run": "m", "run_dir": "r", "scaler": "s", "experiment": None}
    assert plan_hosts(None, "ws-etw-0", [], one, {}) == [{"host": None, **one}], "old pointer: one engine as before"
    ptr = [{"host": "a", "run": "x", "run_dir": "r1", "scaler": "s1", "experiment": None},
           {"host": "b", "run": "y", "run_dir": "r2", "scaler": "s2", "experiment": None}]
    assert plan_hosts(None, "ws-etw-0", ptr, one, {"a": "x", "b": "y"}) == ptr, "the pointer beats the default host"
    assert plan_hosts(["a", "b"], None, None, one, {}) == [{"host": "a", **one}, {"host": "b", **one}]
    assert "repeated" in plan_hosts(["a", "a"], None, None, one, {})
    assert "no model" in plan_hosts(["z"], None, ptr, one, {"a": "x"})
    tmp.cleanup()
    print("[SELFTEST OK] /healthz 200/503, watchdog exits on a dead thread or hung poll, not on Loki being down, "
          "/api/history range (ISO / ms) + alerts-only + 400/404 + traversal refused, ?host= per VM, trained-for text, campaigns, "
          "which engines start")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
