#!/usr/bin/env python3
"""loki_source.py - feed engine.Engine from Loki instead of the HTTP shipper (live_server.py --loki-url).

    etwcollect (VM) -> Loki {job="silketw", split="test", run, provider}
      -> poll every --poll-s: newest run in the split = the current session (a new run = a new session)
      -> per provider stream: query [cursor - overlap, now + grace), drop lines already seen
      -> engine.ingest({session: run, batches: [{stream: provider, offset_start, lines, ends}], clock_ms, dos_map})

Same payload the shipper sends, so engine.py is unchanged: offsets are synthetic (running UTF-8 byte count of
the lines delivered per provider), so ingest stays idempotent; the dos map comes from the run's etw-meta line.

Clock (deviation D8, docs/DEVIATIONS.md): there is no VM heartbeat through Loki. clock_ms = newest event time
seen. When no new line arrives for --idle-s, the clock keeps moving with the host's wall time minus the last
measured lag (host now - newest event), so the last window of a stopped capture still closes.

Why poll instead of /tail (websocket): no extra dependency, and reconnecting is just asking again from the
cursor. Why the overlap: the VM ships each file in order, but small reorderings inside a file are possible;
re-reading the last few seconds and de-duplicating costs little.

    uv run python src/live/loki_source.py --selftest
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pipeline"))
from lokiclient import Loki, selector  # noqa: E402

NS, MS = 1_000_000_000, 1_000_000


class LokiSource:
    def __init__(self, engine, url, split="test", poll_s=1.0, idle_s=40.0, overlap_s=10.0, lookback_s=3600.0,
                 grace_s=600.0, max_lines=20_000, now_ns=time.time_ns, log=print, host=None):
        self.engine, self.lk, self.split = engine, Loki(url), split
        self.only_host = host   # follow this Loki host label only; None = newest run of any host
        self.poll_s, self.idle_ns, self.overlap_ns = poll_s, int(idle_s * NS), int(overlap_s * NS)
        self.lookback_ns, self.grace_ns, self.now_ns, self.log = int(lookback_s * NS), int(grace_s * NS), now_ns, log
        self.run = self.host = None
        self.started_ns = now_ns()
        self.lag_ns = self.clock_ms = None
        self.last_new = now_ns()
        self.last_poll = self.last_error = self.last_ok = None
        # catch-up cap (24/7): after a restart or a Loki read outage the backlog can be ~1 h of lines; reading it
        # in one step would hold every line + parsed event in memory at once. At most max_lines per provider
        # per step; the cursor/overlap logic resumes where it stopped.
        self.max_lines, self.catching_up = max_lines, False
        self._stop = threading.Event()
        self.thread = None

    # ------------------------------------------------------------ per-run state
    def _reset(self, run, host, first_ns):
        self.run, self.host = run, host
        self.cursor = {}        # provider -> newest ts delivered (ns)
        self.seen = {}          # provider -> {(ts, line)} with ts >= cursor - overlap
        self.offset = {}        # provider -> synthetic byte offset
        self.first_ns = first_ns
        self.clock_ms = self.lag_ns = None
        self.last_new = self.now_ns()
        rows = self.lk.query_range(selector(job="etw-meta", host=self.only_host, run=run), first_ns - 86400 * NS,
                                   self.now_ns() + self.grace_ns, limit=1, direction="backward")
        self.dmap = json.loads(rows[0][2]) if rows else {}
        self.log(f"[loki] following run {run} ({self.split}, host {host})  dos_map={self.dmap or 'none (etw-meta missing)'}")

    def _current_run(self, now):
        rows = self.lk.query_range(selector(job="silketw", host=self.only_host, split=self.split), now - self.lookback_ns,
                                   now + self.grace_ns, limit=1, direction="backward")
        if not rows:
            return None
        lab = rows[0][1]
        if lab["run"] != self.run:
            edge = self.lk.edge_ts(selector(job="silketw", host=self.only_host, split=self.split, run=lab["run"]),
                                   now - self.lookback_ns, now + self.grace_ns)
            self._reset(lab["run"], lab["host"], edge[0] if edge else now)
        return self.run

    # ------------------------------------------------------------ one poll
    def step(self):
        now = self.now_ns()
        self.last_poll = now
        if self._current_run(now) is None:
            return 0
        provs = self.lk.label_values("provider", self.first_ns - NS, now + self.grace_ns,
                                     query=selector(job="silketw", host=self.only_host, split=self.split, run=self.run))
        batches, newest, truncated = [], None, {}
        for p in sorted(provs):
            cur = self.cursor.get(p)
            start = self.first_ns if cur is None else cur - self.overlap_ns
            seen = self.seen.setdefault(p, set())
            lines, ends = [], []
            off = self.offset.get(p, 0)
            for ts, _, line in self.lk.query_all(selector(job="silketw", host=self.only_host, split=self.split, run=self.run, provider=p),
                                                 start, now + self.grace_ns):
                if (ts, line) in seen:
                    continue
                seen.add((ts, line))
                off += len(line.encode("utf-8")) + 1
                lines.append(line)
                ends.append(off)
                cur = ts if cur is None else max(cur, ts)
                if self.max_lines and len(lines) >= self.max_lines:
                    truncated[p] = cur              # more lines of p are pending in Loki
                    break
            if lines:
                batches.append({"stream": p, "offset_start": self.offset.get(p, 0), "lines": lines, "ends": ends})
                self.offset[p], self.cursor[p] = off, cur
                newest = cur if newest is None else max(newest, cur)
            if cur is not None:
                self.seen[p] = {k for k in seen if k[0] >= cur - self.overlap_ns}
        self.catching_up = bool(truncated)
        if newest is not None:
            self.lag_ns = now - newest
            # a truncated provider is behind: the clock must not pass its cursor, or its pending lines (older
            # than the other providers' newest) would be dropped as late by the engine
            # (newest over ALL providers' cursors, not just this step's: once the backlog drains, a provider that
            # was already up to date must lift the clock again; without truncation both are the same)
            clock_ns = min([max(self.cursor.values())] + list(truncated.values()))
            self.clock_ms = max(self.clock_ms or 0, clock_ns // MS)
            self.last_new = now
        elif self.clock_ms is not None and now - self.last_new > self.idle_ns:
            self.clock_ms = max(self.clock_ms, (now - (self.lag_ns or 0)) // MS)   # idle: follow the wall clock
        n = sum(len(b["lines"]) for b in batches)
        if self.clock_ms is None:
            return n
        code, resp = self.engine.ingest({"session": self.run, "run": self.run, "mode": "loki", "host": self.host,
                                         "dos_map": self.dmap, "exclude_pids": [], "clock_ms": self.clock_ms,
                                         "batches": batches})
        if code == 409:                              # engine restarted its session: replay this run from 0
            self.log(f"[loki] engine expects {resp.get('expected')}; re-reading run {self.run} from the start")
            self.run = None
        elif code != 200:
            raise OSError(f"engine.ingest {code}: {resp}")
        return n

    # ------------------------------------------------------------ thread
    def run_forever(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                n = self.step()
                self.last_error, self.last_ok, backoff = None, self.now_ns(), 1.0
                if n < 5000 and not self.catching_up:
                    self._stop.wait(self.poll_s)
            except Exception as e:           # 24/7: ANY error must not end this thread (the UI would freeze
                self.last_error = f"{type(e).__name__}: {e}"   # while the process looks healthy to systemd)
                if not isinstance(e, OSError):                 # network errors are expected; the rest is a bug
                    traceback.print_exc()
                self.log(f"[loki] poll failed ({self.last_error}); retry in {backoff:.0f}s")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)

    def start(self):
        self.thread = threading.Thread(target=self.run_forever, daemon=True, name="loki-source")
        self.thread.start()
        return self

    def health(self, max_age_s=120.0):
        """(ok, reason) for live_server's /healthz + watchdog: the thread runs and a step succeeded recently."""
        if self.thread is not None and not self.thread.is_alive():
            return False, "loki-source thread died"
        ref = self.last_ok if self.last_ok is not None else self.started_ns
        age = (self.now_ns() - ref) / NS
        if age > max_age_s:
            return False, f"no successful Loki poll for {age:.0f}s (last error: {self.last_error})"
        return True, "ok"

    def stuck(self, max_age_s=300.0):
        """Reason string when the thread is dead or no poll was even ATTEMPTED for max_age_s (a hang), else None.
        Loki being down is not 'stuck' (polls keep failing and retrying): restarting would not fix Loki."""
        if self.thread is not None and not self.thread.is_alive():
            return "loki-source thread died"
        ref = self.last_poll if self.last_poll is not None else self.started_ns
        age = (self.now_ns() - ref) / NS
        return f"no Loki poll attempted for {age:.0f}s (hung?)" if age > max_age_s else None

    def stop(self):
        self._stop.set()

    def status(self):
        now = self.now_ns()
        return {"kind": "loki", "url": self.lk.url, "split": self.split, "run": self.run,
                "polled_s_ago": round((now - self.last_poll) / NS, 1) if self.last_poll else None,
                "lag_s": round(self.lag_ns / NS, 1) if self.lag_ns is not None else None,
                "idle": bool(self.run and now - self.last_new > self.idle_ns), "error": self.last_error,
                "last_ok_s_ago": round((now - self.last_ok) / NS, 1) if self.last_ok else None,
                "catching_up": self.catching_up}


# --------------------------------------------------------------------------- selftest
def selftest():
    """Fake Loki + a recording stand-in for Engine: each line delivered once with monotonic offsets, late
    lines inside the overlap still arrive, a new run starts a new session, the idle clock advances."""
    from lokiclient import FakeLoki

    class Rec:
        def __init__(self):
            self.calls = []

        def ingest(self, p):
            self.calls.append(json.loads(json.dumps(p)))
            return 200, {}
    fake = FakeLoki()
    try:
        lk = Loki(fake.url)
        clock = [1_791_532_594 * NS]
        t0 = clock[0] - 60 * NS
        lab = lambda run, p: {"job": "silketw", "host": "ws-etw", "split": "test", "run": run, "provider": p}  # noqa: E731
        lk.push([{"stream": {"job": "etw-meta", "host": "ws-etw", "split": "test", "run": "live-1"},
                  "values": [[str(t0), json.dumps({"\\device\\harddiskvolume4": "c:"})]]}])
        lk.push([{"stream": lab("live-1", "kproc"), "values": [[str(t0 + i * NS), f"p{i}"] for i in range(3)]},
                 {"stream": lab("live-1", "kfile"), "values": [[str(t0 + i * NS), f"f{i}"] for i in range(2)]}])
        eng = Rec()
        src = LokiSource(eng, fake.url, idle_s=30, overlap_s=10, now_ns=lambda: clock[0], log=lambda *_: None)
        st = src.status()                                  # before any run is found (UI polls this)
        assert st["run"] is None and st["lag_s"] is None and not st["idle"], st
        assert src.step() == 5
        c = eng.calls[-1]
        assert c["session"] == "live-1" and c["dos_map"] == {"\\device\\harddiskvolume4": "c:"}
        kp = next(b for b in c["batches"] if b["stream"] == "kproc")
        assert kp["lines"] == ["p0", "p1", "p2"] and kp["offset_start"] == 0 and kp["ends"] == [3, 6, 9]
        assert c["clock_ms"] == (t0 + 2 * NS) // MS
        # a late kproc line 5 s BEFORE the cursor (inside the overlap) + a new one; nothing resent
        clock[0] += 2 * NS
        lk.push([{"stream": lab("live-1", "kproc"), "values": [[str(t0 + 1 * NS + 5), "late"], [str(t0 + 5 * NS), "p5"]]}])
        assert src.step() == 2
        kp = eng.calls[-1]["batches"][0]
        assert kp["lines"] == ["late", "p5"] and kp["offset_start"] == 9 and kp["ends"] == [14, 17]
        assert src.step() == 0 and eng.calls[-1]["batches"] == []
        # idle: no lines for > 30 s -> clock follows the wall clock minus the measured lag
        before = eng.calls[-1]["clock_ms"]
        clock[0] += 40 * NS
        src.step()
        assert eng.calls[-1]["clock_ms"] > before + 30_000 and src.status()["idle"]
        # a newer run appears -> new session, offsets restart at 0
        lk.push([{"stream": lab("live-2", "kproc"), "values": [[str(clock[0] - NS), "q0"]]}])
        assert src.step() == 1 and eng.calls[-1]["session"] == "live-2"
        assert eng.calls[-1]["batches"][0]["offset_start"] == 0

        # bounded catch-up: a 25-line kfile backlog with max_lines 10 arrives over 3 steps, each line once, and the
        # clock never passes the kfile cursor while kfile is behind (kproc is already at the newest time)
        clock[0] += 120 * NS
        t3 = clock[0] - 60 * NS
        lk.push([{"stream": lab("live-3", "kfile"), "values": [[str(t3 + i * NS), f"b{i}"] for i in range(25)]},
                 {"stream": lab("live-3", "kproc"), "values": [[str(t3 + 50 * NS), "k-new"]]}])
        eng3 = Rec()
        src3 = LokiSource(eng3, fake.url, idle_s=30, overlap_s=10, max_lines=10, now_ns=lambda: clock[0],
                          log=lambda *_: None)
        got = []
        for _ in range(5):
            src3.step()
            c = eng3.calls[-1]
            kf = [b for b in c["batches"] if b["stream"] == "kfile"]
            got += kf[0]["lines"] if kf else []
            if kf and src3.catching_up:
                assert c["clock_ms"] <= (t3 + (len(got) - 1) * NS) // MS, (c["clock_ms"], len(got))
        assert got == [f"b{i}" for i in range(25)], got
        assert not src3.catching_up and eng3.calls[-1]["clock_ms"] == (t3 + 50 * NS) // MS
        assert sum(1 for c in eng3.calls if any(b["stream"] == "kfile" for b in c["batches"])) == 3

        # 24/7: an unexpected exception (not OSError) must not end the polling thread
        class Boom(Rec):
            n = 0

            def ingest(self, p):
                Boom.n += 1
                if Boom.n == 1:
                    raise RuntimeError("converter bug")
                return super().ingest(p)
        eng4 = Boom()
        src4 = LokiSource(eng4, fake.url, poll_s=0.05, now_ns=lambda: clock[0], log=lambda *_: None)
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()):      # the expected traceback
            src4.start()
            deadline = time.time() + 10
            while not eng4.calls and time.time() < deadline:
                time.sleep(0.05)
        src4.stop()
        assert Boom.n >= 2 and eng4.calls, "thread died on RuntimeError"
        ok, why = src4.health()
        assert ok, why

        # two Windows hosts: with host= the source stays on its host even when the other host's run is newer
        clock[0] += 120 * NS
        t5 = clock[0] - 30 * NS
        lk.push([{"stream": lab("live-5", "kproc"), "values": [[str(t5), "mine"]]},
                 {"stream": dict(lab("live-6", "kproc"), host="ws-etw-1"), "values": [[str(t5 + NS), "other"]]}])
        eng5 = Rec()
        src5 = LokiSource(eng5, fake.url, now_ns=lambda: clock[0], log=lambda *_: None, host="ws-etw")
        assert src5.step() == 1 and eng5.calls[-1]["session"] == "live-5" and src5.host == "ws-etw"
        assert eng5.calls[-1]["batches"][0]["lines"] == ["mine"]
    finally:
        fake.close()
    print("[SELFTEST OK] once-only delivery, synthetic offsets, overlap catches late lines, idle clock, new run, "
          "bounded catch-up holds the clock, thread survives a non-OSError")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
