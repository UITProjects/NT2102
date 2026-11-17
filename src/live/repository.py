#!/usr/bin/env python3
"""
repository.py - the persistence ports of the live server: WHAT is stored, not WHERE. [OURS] engineering (no effect
on how a window is scored).

The engine, the cross-host correlator and the HTTP layer depend only on these three abstract classes (the Python
counterpart of Spring Data repository interfaces; an ABC = a Java abstract class: @abstractmethod = the contract,
plain methods = shared logic every implementation inherits):

    HistoryRepository     every window state change (history.jsonl): append / get / query a time range / detail
    CrosshostRepository   cross-host follow-ups of red windows (crosshost.jsonl): load all / append one
    SessionStore          the engine's own files: session.json, raw/<stream>.json, windows.csv, windows/<wid>/*,
                          llm_verdicts.csv, the daily LLM budget

Implementations
    files      history.FileHistoryRepository, file_store.FileSessionStore / FileCrosshostRepository:
               the sessions/ folder layout the server has always written (behaviour unchanged by the refactor)
    snapshot   snapshot.Snapshot*: read-only views of a recorded snapshot archive (deploy/snapshot/*.tar.gz, unpacked
               at start); every write raises ReadOnlyRepositoryError. Used by the public demo (deploy/README.md).

open_repositories() is the one place that picks an implementation.

Usage (from the repo root)
    uv run python src/live/repository.py --selftest
"""
from __future__ import annotations

import re
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

SAFE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")      # session folder / window id: no '/', no leading '.'
STATUS_RANK = {"scored": 4, "error": 3, "empty": 2, "partial": 1}
META_KEYS = ("sdir", "session", "mode", "moderate_from")         # history-row keys that are not window fields


class ReadOnlyRepositoryError(RuntimeError):
    """A write on a read-only (snapshot) repository."""


def check_name(*names: str) -> None:
    """Refuse anything that is not a plain folder name (path traversal: '..', '/x', 'a/b')."""
    for n in names:
        if not (isinstance(n, str) and SAFE_NAME.match(n)):
            raise ValueError("bad session or window name")


def restart_n(sdir: str) -> int:
    """'live-...-ws-etw-1.r3' -> 3; a first run (no .rN) -> 1."""
    m = re.search(r"\.r(\d+)$", sdir)
    return int(m.group(1)) if m else 1


# --------------------------------------------------------------------------- history
class HistoryRepository(ABC):
    """Window history. self.rows: (sdir, window id) -> the LATEST row of that window; a row is
    {"sdir", "session", "mode", "moderate_from", **window record} (engine.py builds the window record).
    Subclasses fill self.rows when they load and implement append + _read_detail."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows: dict[tuple[str, str], dict] = {}

    # ---------------------------------------------------------------- contract
    @abstractmethod
    def append(self, row: dict) -> None:
        """Store one window state; the last row of a (sdir, window) pair wins."""

    @abstractmethod
    def _read_detail(self, sdir: str, wid: str) -> dict | None:
        """The stored UI detail of one window (detail.json shape) or None. Names are already checked."""

    # ---------------------------------------------------------------- shared read logic
    def get(self, sdir: str, wid: str) -> dict | None:
        with self.lock:
            r = self.rows.get((sdir, wid))
            return dict(r) if r else None

    def query(self, from_ms: int, to_ms: int, alert_only: bool = False, include_replay: bool = False,
              limit: int = 2000, host: str | None = None) -> dict:
        """Windows overlapping [from_ms, to_ms), one row per (host, window start).

        Live sessions (mode 'loki' from Loki, 'live' from the HTTP shipper) by default; replays re-use past
        timestamps, so they are left out unless include_replay. host = only that host's windows; rows written
        before two-host scoring have no host and match no host filter. A restart re-reads up to 1 h into a new
        <run>.rN folder, so the same window can exist twice: scored > error > empty > partial, then the later restart."""
        with self.lock:
            cand = [r for r in self.rows.values()
                    if isinstance(r.get("start_ms"), int) and r["start_ms"] < to_ms and r.get("end_ms", 0) > from_ms
                    and (include_replay or r.get("mode") != "replay")
                    and (host is None or r.get("host") == host)]
        best: dict[tuple, tuple] = {}
        dups = 0
        for r in cand:
            k = (r.get("host") or "", r["start_ms"])         # two hosts share the window grid
            rank = (STATUS_RANK.get(r.get("status"), 0), restart_n(r["sdir"]), r["sdir"])
            if k in best:
                dups += 1
                if rank <= best[k][0]:
                    continue
            best[k] = (rank, r)
        rows = [r for _, r in sorted(best.values(), key=lambda x: (x[1]["start_ms"], x[1].get("host") or ""))]
        if alert_only:
            rows = [r for r in rows if r.get("alert")]
        return {"from_ms": from_ms, "to_ms": to_ms, "total": len(rows), "duplicates_hidden": dups,
                "truncated": len(rows) > limit, "windows": rows[-limit:] if limit else rows}

    def detail(self, sdir: str, wid: str) -> dict | None:
        """UI detail + the history row's window fields + {"history": session meta}; None if neither exists."""
        check_name(sdir, wid)
        row = self.get(sdir, wid)
        d = self._read_detail(sdir, wid)
        if d is None and row is None:
            return None
        d = d or {}
        win = {k: v for k, v in (row or d.get("window") or {}).items() if k not in META_KEYS}
        return {**d, "window": win, "history": {k: (row or {}).get(k) for k in META_KEYS}}

    def sessions(self) -> list[str]:
        """Every session folder name that has at least one row, sorted."""
        with self.lock:
            return sorted({s for s, _ in self.rows})

    def bounds(self) -> tuple[int, int] | None:
        """(first window start, last window end) in epoch ms over every row, or None when empty."""
        with self.lock:
            st = [r["start_ms"] for r in self.rows.values() if isinstance(r.get("start_ms"), int)]
            en = [r.get("end_ms", 0) for r in self.rows.values() if isinstance(r.get("start_ms"), int)]
        return (min(st), max(en)) if st else None


# --------------------------------------------------------------------------- cross-host findings
class CrosshostRepository(ABC):
    """Cross-host findings (correlate.py). The correlator keeps them in memory; this stores them."""

    @abstractmethod
    def load(self) -> dict[str, dict]:
        """Every stored finding: id -> the LAST stored version of it."""

    @abstractmethod
    def append(self, finding: dict) -> None:
        """Store one (new or updated) finding; the last one of an id wins on load."""


# --------------------------------------------------------------------------- the engine's own files
class SessionStore(ABC):
    """Per-session + per-window files the engine writes while it scores. A session is a folder NAME (sdir, e.g.
    'live-20261115T151036Z-ws-etw-1.r2'); path(sdir) says where it lives (the UI shows it; alg2 / correlate read
    and write window folders by path)."""

    @abstractmethod
    def path(self, sdir: str = "") -> str:
        """Folder of a session ('' = the store root)."""

    @abstractmethod
    def create_session(self, name: str, meta: dict, index_header: list[str]) -> str:
        """New session folder for run `name` -> its sdir. A folder that already holds a session gets a fresh
        <name>.rN (a restart re-reads the run). Writes meta (+ restart_of) as session.json and the index header."""

    @abstractmethod
    def open_raw(self, sdir: str, stream: str):
        """Append-mode text handle for the verbatim raw lines of one stream (caller flushes / closes)."""

    @abstractmethod
    def window_dir(self, sdir: str, wid: str) -> str:
        """Folder of one window (created)."""

    @abstractmethod
    def write_window(self, sdir: str, wid: str, files: dict) -> None:
        """files: name -> pd.DataFrame | (pd.DataFrame, to_csv kwargs) | dict/list (JSON) | callable(path)."""

    @abstractmethod
    def append_index(self, sdir: str, row: list) -> None:
        """One row of windows.csv."""

    @abstractmethod
    def append_verdict(self, sdir: str, row: list) -> None:
        """One row of llm_verdicts.csv (header written on first use)."""

    @abstractmethod
    def save_detail(self, sdir: str, wid: str, detail: dict) -> None:
        """detail.json, atomically (a reader never sees half a file)."""

    @abstractmethod
    def load_detail(self, sdir: str, wid: str) -> dict | None:
        """detail.json or None."""

    @abstractmethod
    def budget(self, today: str) -> dict:
        """Today's LLM budget use: {"date", "used"} (another day's count reads as 0)."""

    @abstractmethod
    def take_budget(self, limit: int, today: str) -> bool:
        """Count one investigation against today's budget; False = spent (nothing counted). limit 0 = no limit."""


# --------------------------------------------------------------------------- factory
@dataclass
class Repositories:
    history: HistoryRepository
    crosshost: CrosshostRepository
    sessions: SessionStore
    manifest: dict | None = None          # snapshot only: what the archive contains (snapshot.build writes it)


def open_repositories(mode: str, path: str, data_dir: str | None = None) -> Repositories:
    """mode 'files': path = the sessions folder (read + write).
    mode 'snapshot': path = a snapshot .tar.gz, unpacked into data_dir (read-only)."""
    if mode == "files":
        from file_store import FileCrosshostRepository, FileSessionStore
        from history import FileHistoryRepository
        return Repositories(FileHistoryRepository(path), FileCrosshostRepository(path), FileSessionStore(path))
    if mode == "snapshot":
        import snapshot
        return snapshot.open_snapshot(path, data_dir)
    raise ValueError(f"unknown repository mode {mode!r} (files | snapshot)")


# --------------------------------------------------------------------------- selftest
def selftest():
    """The shared logic of the base classes, on an in-memory subclass. Implementations test themselves
    (history.py, file_store.py, snapshot.py)."""

    class Mem(HistoryRepository):
        def __init__(self):
            super().__init__()
            self.details = {}

        def append(self, row):
            with self.lock:
                self.rows[(row["sdir"], str(row["window"]))] = dict(row)

        def _read_detail(self, sdir, wid):
            return self.details.get((sdir, wid))

    try:
        HistoryRepository()                                       # abstract: cannot be built
        raise AssertionError("abstract class instantiated")
    except TypeError:
        pass
    h = Mem()
    base = 1_794_700_000_000
    w = lambda sdir, k, st="scored", host="a", alert=False: {"sdir": sdir, "session": sdir, "mode": "loki",
                                                            "moderate_from": 4.5, "window": f"w{k}", "host": host,
                                                            "start_ms": base + k * 300_000,
                                                            "end_ms": base + (k + 1) * 300_000, "status": st,
                                                            "alert": alert}
    h.append(w("s", 0, "partial"))
    h.append(w("s.r2", 0, "scored"))                              # the restart re-scored it: wins
    h.append(w("s", 1, alert=True))
    h.append(w("sb", 1, host="b"))
    q = h.query(base, base + 3 * 300_000)
    assert q["total"] == 3 and q["duplicates_hidden"] == 1, q
    assert [r["sdir"] for r in q["windows"]][:1] == ["s.r2"], q
    assert h.query(base, base + 3 * 300_000, alert_only=True)["total"] == 1
    assert h.query(base, base + 3 * 300_000, host="b")["total"] == 1
    assert h.bounds() == (base, base + 2 * 300_000) and h.sessions() == ["s", "s.r2", "sb"]
    h.details[("s", "w1")] = {"top": {}, "window": {"window": "w1"}}
    d = h.detail("s", "w1")
    assert d["window"]["alert"] is True and d["history"]["moderate_from"] == 4.5 and "sdir" not in d["window"], d
    assert h.detail("s", "nope") is None
    for bad in ("..", "../x", "a/b", ".hidden"):
        try:
            h.detail(bad, "w1")
            raise AssertionError(f"accepted {bad!r}")
        except ValueError:
            pass
    assert restart_n("x.r12") == 12 and restart_n("x") == 1
    try:
        open_repositories("nope", ".")
        raise AssertionError("unknown mode accepted")
    except ValueError:
        pass
    print("[repository] selftest OK (abstract base, query dedup, detail merge, name guard, factory)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
