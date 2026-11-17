#!/usr/bin/env python3
"""
playback.py - snapshot mode of the live server: recorded windows replayed over the same HTTP/SSE API, no model,
no torch. [OURS] demo engineering (deploy/README.md); nothing is scored here.

    SnapshotMember   stands in for engine.Engine behind live_server.create_app (same duck-typed interface the
                     live_server selftest stubs already use: listeners, status, windows, window_detail, health,
                     history, session). One per recorded host.
    Player           one thread: walks the recorded windows of a playback range in time order, one 5-min slot every
                     interval_s seconds, and hands each host its windows (final recorded state: score, level, LLM
                     verdict). At the end it pauses, then starts a new loop (a new session id, so the UI clears).
    SnapshotCorrelator  read-only view of the recorded cross-host findings (correlate.Correlator's read side).

Usage (from the repo root)
    uv run python src/live/playback.py --selftest
"""
from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from repository import META_KEYS, Repositories  # noqa: E402

CORRELATION_LEVELS = ("pending", "one-sided", "corroborated")


def _empty_session_counters() -> dict:
    """The live-session fields the UI reads; zero / empty, because nothing is ingested in a snapshot."""
    return {"last_seen_s_ago": 0.0, "event_lag_s": None, "clock_skew_s": None, "lines_per_s": 0, "lines_total": {},
            "heap": 0, "late_dropped": 0, "bad_lines": 0, "bad_events": {}, "events": {}, "edges": {}, "excluded": {},
            "unresolved": {}, "unmapped": 0, "exclude_pids": [], "dos_map": {}, "scoring_queue": 0}


class SnapshotMember:
    read_only = True

    def __init__(self, host: str | None, repos: Repositories, first_sdir: str | None, snapshot_info: dict):
        self.host, self.repos, self.history = host, repos, repos.history
        self.info = snapshot_info
        self.listeners = []
        self.lock = threading.Lock()
        self.loop = 0
        self.played: list[dict] = []                     # rows played in this loop, in order
        self.by_id: dict[str, dict] = {}
        self.clock_ms = None
        self.next_ms = None
        self.sdir = first_sdir                           # the session whose parameters the status shows
        self.session = None
        self._new_session()

    # ---------------------------------------------------------------- driven by the Player
    def _new_session(self):
        self.loop += 1
        # dir "" never equals a history sdir, so /api/history/{sdir}/{wid} always reads the snapshot files
        self.session = {"id": f"playback-{self.host or 'host'}-{self.loop}", "dir": "", "mode": "snapshot"}

    def restart(self):
        with self.lock:
            self.played, self.by_id, self.clock_ms = [], {}, None
            self._new_session()
        self._emit({"type": "session", "session": self.session["id"]})

    def play(self, row: dict, next_ms: int | None):
        rec = {k: v for k, v in row.items() if k not in META_KEYS}
        rec["_sdir"] = row["sdir"]
        with self.lock:
            self.played.append(rec)
            self.by_id[str(rec["window"])] = rec
            self.clock_ms = rec["end_ms"]
            self.next_ms = next_ms
            self.sdir = row["sdir"]
        self._emit({"type": "window", "window": self._public(rec)})

    def tick(self, clock_ms: int, next_ms: int | None):
        with self.lock:
            self.clock_ms, self.next_ms = clock_ms, next_ms

    # ---------------------------------------------------------------- the engine read interface
    @staticmethod
    def _public(rec: dict) -> dict:
        return {k: v for k, v in rec.items() if k != "_sdir"}

    def windows(self) -> list:
        with self.lock:
            return [self._public(r) for r in self.played]

    def window_detail(self, wid: str) -> dict | None:
        with self.lock:
            rec = self.by_id.get(wid)
        if rec is None:
            return None
        d = self.history.detail(rec["_sdir"], wid)
        return None if d is None else {**d, "window": self._public(rec)}

    def health(self):
        return True, "ok"

    def status(self) -> dict:
        with self.lock:
            sdir, clock, nxt, n = self.sdir, self.clock_ms, self.next_ms, len(self.played)
            red = sum(1 for r in self.played if r.get("alert"))
            attack = sum(1 for r in self.played if r.get("alert") and (r.get("llm") or {}).get("status") == "attack")
            sid = self.session["id"]
        meta = self.repos.sessions.session_meta(sdir) if sdir else {}
        run = meta.get("run_dir") or "unknown"
        return {"host": self.host,
                "model": {"run_dir": run, "tau": meta.get("tau") or {}, "scaler": meta.get("scaler"),
                          "converter_sha256": meta.get("converter_sha256"),
                          "converter_check": meta.get("converter_check") or "recorded",
                          "train": (self.repos.manifest or {}).get("models", {}).get(run, {}),
                          "score_range": meta.get("score_range") or {}},
                "params": {"window_min": meta.get("window_min", 5), "stride_min": meta.get("stride_min", 5),
                           "lateness_s": meta.get("lateness_s"), "origin_ms": meta.get("origin_ms", 0),
                           "alg1": meta.get("alg1") or {}, "llm": {k: v for k, v in (meta.get("llm") or {}).items()
                                                                  if k in ("provider", "model")}},
                "server_ms": int(time.time() * 1000),
                "session": {"id": sid, "mode": "snapshot", "host": self.host or "", "dir": sdir or "",
                            **_empty_session_counters(), "clock_ms": clock, "watermark_ms": clock,
                            "max_event_ms": clock,
                            "open_window": {"start_ms": nxt, "end_ms": nxt + 300_000} if nxt else None,
                            "windows": n, "played_red": red, "played_llm_attack": attack, "loop": self.loop},
                "snapshot": self.info}

    def ingest(self, payload):
        return 403, {"error": "read-only snapshot: nothing is ingested"}

    def _emit(self, ev):
        for cb in list(self.listeners):
            try:
                cb(ev)
            except Exception:
                pass


class Player:
    """Walks the recorded windows in [from_ms, to_ms) slot by slot (all hosts of a slot together)."""

    def __init__(self, members: list[SnapshotMember], repos: Repositories, from_ms: int, to_ms: int,
                 interval_s: float = 3.0, pause_s: float = 30.0, sleep=time.sleep):
        self.members = {m.host: m for m in members}
        self.interval_s, self.pause_s, self.sleep = interval_s, pause_s, sleep
        rows = repos.history.query(from_ms, to_ms, limit=0)["windows"]
        rows = [r for r in rows if r.get("host") in self.members or (None in self.members and not r.get("host"))]
        self.slots: list[tuple[int, list[dict]]] = []
        for r in rows:
            if self.slots and self.slots[-1][0] == r["start_ms"]:
                self.slots[-1][1].append(r)
            else:
                self.slots.append((r["start_ms"], [r]))
        self.thread = None
        self.stop = threading.Event()

    def run_once(self):
        """One loop: every slot, then the pause. The members keep their windows until the next loop's restart."""
        for i, (start, rows) in enumerate(self.slots):
            if self.stop.is_set():
                return
            nxt = self.slots[i + 1][0] if i + 1 < len(self.slots) else None
            for r in rows:
                self.members.get(r.get("host") or None, next(iter(self.members.values()))).play(r, nxt)
            for m in self.members.values():                  # a host with no window in this slot still moves on
                if all((r.get("host") or None) != m.host for r in rows):
                    m.tick(start + 300_000, nxt)
            self.sleep(self.interval_s)
        self.sleep(self.pause_s)

    def _loop(self):
        while not self.stop.is_set():
            self.run_once()
            for m in self.members.values():
                m.restart()

    def start(self) -> "Player":
        self.thread = threading.Thread(target=self._loop, daemon=True, name="playback")
        self.thread.start()
        return self


class SnapshotCorrelator:
    """correlate.Correlator's read side over recorded findings; never opens a new one."""

    def __init__(self, repo, hosts: list[str]):
        self.findings = repo.load()
        self.hosts = hosts
        self.listeners = []

    def list(self, limit: int = 100) -> list:
        return sorted(self.findings.values(), key=lambda f: f.get("start_ms", 0), reverse=True)[:limit]

    def get(self, fid: str) -> dict | None:
        return self.findings.get(fid)

    def status(self) -> dict:
        return {"hosts": {h: "" for h in self.hosts},
                "findings": {lv: sum(f.get("level") == lv for f in self.findings.values()) for lv in CORRELATION_LEVELS}}


def build_members(repos: Repositories, play_from: int, play_to: int, interval_s: float, pause_s: float,
                  note: str | None = None):
    """-> (members list for create_app, Player (not started), SnapshotCorrelator or None, snapshot info)."""
    man = repos.manifest or {}
    hosts = man.get("hosts") or [None]
    info = {"from_ms": man.get("from_ms"), "to_ms": man.get("to_ms"), "note": note or man.get("note"),
            "generated_utc": man.get("generated_utc"), "counts": man.get("counts"), "code": man.get("code"),
            "playback": {"from_ms": play_from, "to_ms": play_to, "interval_s": interval_s, "pause_s": pause_s}}
    first = {}
    for r in repos.history.query(play_from, play_to, limit=0)["windows"]:
        first.setdefault(r.get("host"), r["sdir"])
    members = [SnapshotMember(h, repos, first.get(h), info) for h in hosts]
    player = Player(members, repos, play_from, play_to, interval_s, pause_s)
    cor = SnapshotCorrelator(repos.crosshost, [h for h in hosts if h]) if len(hosts) > 1 else None
    return members, player, cor, info


# --------------------------------------------------------------------------- selftest
def selftest():
    """'It runs': a two-host snapshot replays slot by slot, details come from the snapshot, the loop restarts."""
    import tempfile
    from file_store import FileCrosshostRepository, FileSessionStore
    from history import FileHistoryRepository
    from repository import open_repositories
    import snapshot
    base, m5 = 1_794_700_000_000, 300_000
    with tempfile.TemporaryDirectory() as d:
        sess = os.path.join(d, "sessions")
        h, st = FileHistoryRepository(sess), FileSessionStore(sess)
        for host in ("ws-etw-0", "ws-etw-1"):
            sdir = f"live-a-{host}"
            os.makedirs(os.path.join(sess, sdir))
            import json
            json.dump({"run_dir": f"/x/runs/model-{host}", "tau": {"net": -0.1}, "alg1": {"moderate_from": 4.5},
                       "llm": {"provider": "deepseek", "model": "m", "max_per_day": 30}},
                      open(os.path.join(sess, sdir, "session.json"), "w"))
            for k in range(3 if host == "ws-etw-0" else 2):
                row = {"sdir": sdir, "session": "live-a", "mode": "loki", "moderate_from": 4.5, "window": f"w{k}",
                       "host": host, "start_ms": base + k * m5, "end_ms": base + (k + 1) * m5, "status": "scored",
                       "alert": host == "ws-etw-1" and k == 1, "max_sg_score": 7.2,
                       "llm": {"status": "attack"} if host == "ws-etw-1" and k == 1 else None}
                h.append(row)
                st.save_detail(sdir, f"w{k}", {"top": {"net": []}, "window": row})
        FileCrosshostRepository(sess).append({"id": "f1", "start_ms": base, "level": "one-sided"})
        snapshot.build(sess, base + 3 * m5, os.path.join(d, "s.tar.gz"), log=lambda *_: None)
        repos = open_repositories("snapshot", os.path.join(d, "s.tar.gz"), os.path.join(d, "data"))
        members, player, cor, info = build_members(repos, base, base + 3 * m5, 0, 0)
        player.sleep = lambda s: None
        seen = []
        for m in members:
            m.listeners.append(lambda ev, h=m.host: seen.append((h, ev["type"], ev.get("window", {}).get("window"))))
        assert [m.host for m in members] == ["ws-etw-0", "ws-etw-1"] and len(player.slots) == 3
        assert members[1].status()["model"]["run_dir"] == "model-ws-etw-1", "status before the first window"
        player.run_once()
        assert [w["window"] for w in members[0].windows()] == ["w0", "w1", "w2"]
        assert [w["window"] for w in members[1].windows()] == ["w0", "w1"]
        s1 = members[1].status()
        assert s1["session"]["played_red"] == 1 and s1["session"]["played_llm_attack"] == 1, s1["session"]
        assert s1["session"]["clock_ms"] == base + 3 * m5, "a host with no window in a slot still moves on"
        assert s1["params"]["llm"] == {"provider": "deepseek", "model": "m"} and s1["snapshot"]["counts"]["windows"] == 5
        det = members[1].window_detail("w1")
        assert det["window"]["llm"]["status"] == "attack" and "_sdir" not in det["window"], det
        assert members[0].window_detail("nope") is None and members[0].ingest({})[0] == 403
        assert ("ws-etw-1", "window", "w1") in seen
        for m in members:
            m.restart()
        assert members[0].windows() == [] and members[0].session["id"].endswith("-2")
        assert ("ws-etw-0", "session", None) in seen
        assert cor.list()[0]["id"] == "f1" and cor.status()["findings"]["one-sided"] == 1
    print("[playback] selftest OK (slot order, per-host windows, status from session.json, detail from the snapshot, "
          "ingest refused, loop restart, recorded cross-host findings)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
