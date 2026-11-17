#!/usr/bin/env python3
"""
loadtest.py - a small, realistic load on the demo (a handful of employers browsing at once), standard library only.

Each virtual user does what a visitor does in the UI: opens the page, keeps ONE SSE stream open (the live panels),
then loops: status, the window list of both hosts, a History query (6 h, then 7 d alerts-only), and 3 window
details picked from the History result, with a 2-5 s think time. Reports latency percentiles per request type,
errors, and HTTP 429s (none expected: the Ingress has no rate limit since 2026-11-17).

    python deploy/loadtest.py --url http://localhost:8766 --users 10 --duration 300
    python deploy/loadtest.py --url https://projects.datrollout.dev/nt2102 --users 10 --duration 300

Measure resources meanwhile: `docker stats nt2102-demo` locally, `kubectl top pod -n nt2102-demo` on the cluster.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

UA = {"User-Agent": "nt2102-loadtest/1"}


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.ms = defaultdict(list)
        self.codes = defaultdict(lambda: defaultdict(int))
        self.sse_events = 0

    def add(self, name, ms, code):
        with self.lock:
            self.ms[name].append(ms)
            self.codes[name][code] += 1


def get(base, path, stats, name, timeout=15):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=UA), timeout=timeout) as r:
            body = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        body, code = b"", e.code
    except Exception as e:                       # timeouts, resets
        body, code = b"", type(e).__name__
    stats.add(name, (time.perf_counter() - t0) * 1000, code)
    return json.loads(body) if code == 200 and body[:1] in (b"{", b"[") else None


def sse(base, stats, stop):
    """One open event stream per user, like the browser tab."""
    while not stop.is_set():
        try:
            with urllib.request.urlopen(urllib.request.Request(base + "/api/stream", headers=UA), timeout=30) as r:
                for line in r:
                    if stop.is_set():
                        return
                    if line.startswith(b"event:"):
                        with stats.lock:
                            stats.sse_events += 1
        except Exception:
            time.sleep(1)


def user(base, stats, stop, seed):
    rnd = random.Random(seed)
    threading.Thread(target=sse, args=(base, stats, stop), daemon=True).start()
    get(base, "/", stats, "page")
    st = get(base, "/api/status", stats, "status") or {}
    snap = st.get("snapshot") or {}
    end = snap.get("to_ms") or int(time.time() * 1000)
    hosts = [h["host"] for h in st.get("hosts", []) if h.get("host")]
    while not stop.is_set():
        get(base, "/api/status", stats, "status")
        for h in hosts:
            get(base, "/api/windows?host=" + urllib.parse.quote(h), stats, "windows")
        q = {"from": str(end - 6 * 3600_000), "to": str(end)}
        hist = get(base, "/api/history?" + urllib.parse.urlencode(q), stats, "history_6h") or {}
        get(base, "/api/history?" + urllib.parse.urlencode({"from": str(end - 7 * 86400_000), "to": str(end),
                                                            "alert": "1"}), stats, "history_7d_alerts")
        rows = hist.get("windows") or []
        for w in rnd.sample(rows, min(3, len(rows))):
            get(base, f"/api/history/{w['sdir']}/{w['window']}", stats, "detail")
        stop.wait(rnd.uniform(2, 5))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8766")
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--duration", type=float, default=300, help="seconds")
    a = ap.parse_args()
    base = a.url.rstrip("/")
    stats, stop = Stats(), threading.Event()
    threads = [threading.Thread(target=user, args=(base, stats, stop, i), daemon=True) for i in range(a.users)]
    t0 = time.time()
    for t in threads:
        t.start()
        time.sleep(0.5)                          # staggered arrivals
    try:
        while time.time() - t0 < a.duration:
            time.sleep(5)
            n = sum(len(v) for v in stats.ms.values())
            print(f"[loadtest] {time.time() - t0:5.0f}s  {n} requests  {stats.sse_events} SSE events", flush=True)
    finally:
        stop.set()
    el = time.time() - t0
    print(f"\n{a.users} users, {el:.0f}s, {sum(len(v) for v in stats.ms.values()) / el:.1f} req/s, "
          f"{stats.sse_events} SSE events")
    print(f"{'request':<20}{'n':>7}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}  codes")
    for name, v in sorted(stats.ms.items()):
        v = sorted(v)
        p95 = v[min(len(v) - 1, int(len(v) * 0.95))]
        print(f"{name:<20}{len(v):>7}{statistics.median(v):>9.1f}{p95:>9.1f}{v[-1]:>9.1f}  {dict(stats.codes[name])}")
    bad = {n: {c: k for c, k in cs.items() if c != 200} for n, cs in stats.codes.items()}
    bad = {n: cs for n, cs in bad.items() if cs}
    print("non-200:", bad or "none")


if __name__ == "__main__":
    main()
