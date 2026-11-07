#!/usr/bin/env python3
"""
history.py - persistent, file-based window history of the live server, queryable by time range. [OURS]
engineering for the 24/7 demo (DEVIATIONS D8); nothing here changes how a window is scored.

    sessions/history.jsonl                one JSON line per window state change (closed / scored / error /
                                          LLM verdict): {"sdir", "session", "mode", "moderate_from", **window}
                                          -> the LAST line of a (sdir, window) pair wins
    sessions/<sdir>/windows/<wid>/detail.json   what the UI's detail panel shows (top nodes, subgraphs, LLM)

    startup:  load history.jsonl into memory (~288 windows per day); if it does not exist yet, backfill it once
              from every sessions/*/windows.csv (+ the last verdict of llm_verdicts.csv), so sessions written
              before this file existed are queryable too
    query:    windows overlapping [from, to); one row per window start (a restart re-reads up to 1 h into a
              new <run>.rN folder, so the same window can exist twice): scored > error > empty > partial,
              then the later restart
    detail:   detail.json; for windows scored before it existed, rebuilt from scores.csv + subgraphs.json
              (no LLM summary then)

Why a JSONL file and not a database: the project keeps every stage as plain files you can open (CLAUDE.md §9,
no RDF/GraphDB). The file is append-only, so a crash loses at most the line being written; a torn last line is
skipped on load.

Usage (from the repo root)
    uv run python src/live/history.py --selftest
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pipeline"))
import alg2  # noqa: E402  (display_attr: NET ints -> ip:port for the UI)

HISTORY_FILE = "history.jsonl"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")      # session folder / window id: no '/', no leading '.'
STATUS_RANK = {"scored": 4, "error": 3, "empty": 2, "partial": 1}
META_KEYS = ("sdir", "session", "mode", "moderate_from")


# --------------------------------------------------------------------------- detail (shared with engine.py)
def _f(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def display_attrs(attr, ntype: str) -> dict:
    """UI only: NET attrs decoded from SilkETW's signed integers (alg2.display_attr), raw value kept in
    attr_raw so a shown IP can be traced back to the raw line. Stored files and node IDs stay raw."""
    shown = alg2.display_attr(attr, ntype)
    raw = "" if attr is None or (isinstance(attr, float) and math.isnan(attr)) else str(attr)
    return {"attr": shown} | ({"attr_raw": raw} if shown != raw else {})


def make_detail(nodes, subgraphs: list, sg_stats: dict, truncated: bool, top_k: int = 20) -> dict:
    """The UI detail of one scored window (engine._score and the history fallback build it the same way).

    nodes: pd.DataFrame [N rows: node_uuid, node_type, score, tau, margin, flagged, attr] (score_window + attrs)
    -> {"top": {node_type: [<= top_k rows by margin]}, "subgraphs": first 25 with display attrs, ...}"""
    top = {}
    for t, g in nodes.groupby("node_type"):
        g = g.sort_values("margin", ascending=False).head(top_k)
        top[t] = [{"id": r.node_uuid, **display_attrs(r.attr, t), "score": _f(r.score), "tau": _f(r.tau),
                   "margin": _f(r.margin), "flagged": bool(r.flagged)} for r in g.itertuples()]
    # display copy: subgraphs.json and the LLM job keep the raw attrs
    shown = [{**g, "nodes": [{**n, **display_attrs(n.get("attr"), n["type"])} for n in g["nodes"]]}
             for g in subgraphs[:25]]
    return {"top": top, "subgraphs": shown, "truncated": truncated, "subgraphs_total": len(subgraphs),
            "alg1_stats": sg_stats}


def _json_default(o):
    return o.item() if hasattr(o, "item") else str(o)      # numpy scalars -> int/float


def write_json_atomic(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, default=_json_default)
    os.replace(tmp, path)                                    # a reader never sees half a file


def parse_time_ms(v: str | int | None) -> int | None:
    """'1793704800000' (epoch ms) or ISO-8601 ('2026-11-03T17:30:00Z', '2026-11-03T17:30+07:00'); naive = UTC."""
    if v is None or v == "":
        return None
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    from datetime import datetime, timezone
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def _restart_n(sdir: str) -> int:
    m = re.search(r"\.r(\d+)$", sdir)
    return int(m.group(1)) if m else 1


# --------------------------------------------------------------------------- store
class HistoryStore:
    def __init__(self, sessions_dir: str):
        self.dir = sessions_dir
        self.path = os.path.join(sessions_dir, HISTORY_FILE)
        self.lock = threading.Lock()
        self.rows: dict[tuple[str, str], dict] = {}          # (sdir, window id) -> latest row
        self.bad_lines = 0
        self.backfilled = 0
        os.makedirs(sessions_dir, exist_ok=True)
        if os.path.exists(self.path):
            self._load()
        else:
            self._backfill()

    # ---------------------------------------------------------------- write
    def append(self, row: dict) -> None:
        """row = {"sdir", "session", "mode", "moderate_from", **window record}; one line, flushed."""
        line = json.dumps(row, default=_json_default)
        with self.lock:
            with open(self.path, "a") as fh:
                fh.write(line + "\n")
            self.rows[(row["sdir"], str(row["window"]))] = json.loads(line)

    # ---------------------------------------------------------------- read
    def _load(self):
        with open(self.path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    self.rows[(r["sdir"], str(r["window"]))] = r
                except (ValueError, KeyError):
                    self.bad_lines += 1                      # e.g. a line torn by a crash

    def _backfill(self):
        """Once, when history.jsonl does not exist: index every session folder that has a windows.csv."""
        rows = []
        for sdir in sorted(os.listdir(self.dir)):
            sp = os.path.join(self.dir, sdir)
            if not SAFE_NAME.match(sdir) or not os.path.isfile(os.path.join(sp, "windows.csv")):
                continue
            try:
                meta = json.load(open(os.path.join(sp, "session.json")))
            except (OSError, ValueError):
                meta = {}
            mode = meta.get("mode") or ("replay" if sdir.startswith("replay-") else
                                        "live" if sdir.startswith("live-") else "")
            verdicts = {}
            try:
                with open(os.path.join(sp, "llm_verdicts.csv"), newline="") as fh:
                    for v in csv.DictReader(fh):
                        verdicts[v["window"]] = {"status": v["verdict"],
                                                 "confidence": float(v["confidence"]) if v.get("confidence") else None,
                                                 "tactics": (v.get("tactics") or "").split()}
            except OSError:
                pass
            with open(os.path.join(sp, "windows.csv"), newline="") as fh:
                for w in csv.DictReader(fh):
                    try:
                        rec = {"window": w["window"], "start_ms": int(w["start_ms"]), "end_ms": int(w["end_ms"]),
                               "start_utc": w["start_utc"], "status": w["status"],
                               **{k: int(w[k] or 0) for k in ("edges", "nodes", "flagged", "subgraphs",
                                                              "subgraphs_kept")},
                               "max_sg_score": float(w["max_sg_score"] or 0), "max_level": w["max_level"],
                               "alert": w["alert"] == "True"}
                    except (KeyError, ValueError):
                        continue
                    if w.get("edges_dropped_relation"):
                        rec["edges_dropped_relation"] = int(w["edges_dropped_relation"])
                    if w["window"] in verdicts:
                        rec["llm"] = verdicts[w["window"]]
                    elif w.get("llm"):
                        rec["llm"] = {"status": w["llm"]}
                    rows.append({"sdir": sdir, "session": meta.get("session", sdir), "mode": mode,
                                 "moderate_from": (meta.get("alg1") or {}).get("moderate_from"), **rec,
                                 "backfilled": True})
        with self.lock:
            with open(self.path, "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r, default=_json_default) + "\n")
                    self.rows[(r["sdir"], str(r["window"]))] = r
        self.backfilled = len(rows)
        print(f"[history] backfilled {len(rows)} windows from {len({r['sdir'] for r in rows})} session folder(s) "
              f"-> {self.path}")

    def get(self, sdir: str, wid: str) -> dict | None:
        with self.lock:
            r = self.rows.get((sdir, wid))
            return dict(r) if r else None

    def query(self, from_ms: int, to_ms: int, alert_only: bool = False, include_replay: bool = False,
              limit: int = 2000, host: str | None = None) -> dict:
        """Live sessions (mode 'loki' from Loki, 'live' from the HTTP shipper) by default; replays re-use past
        timestamps, so they are left out unless include_replay. host = only that host's windows; rows written
        before two-host scoring have no host and match no host filter."""
        with self.lock:
            cand = [r for r in self.rows.values()
                    if isinstance(r.get("start_ms"), int) and r["start_ms"] < to_ms and r.get("end_ms", 0) > from_ms
                    and (include_replay or r.get("mode") != "replay")
                    and (host is None or r.get("host") == host)]
        best: dict[tuple, tuple] = {}
        dups = 0
        for r in cand:
            k = (r.get("host") or "", r["start_ms"])         # two hosts share the window grid
            rank = (STATUS_RANK.get(r.get("status"), 0), _restart_n(r["sdir"]), r["sdir"])
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

    # ---------------------------------------------------------------- detail
    def window_dir(self, sdir: str, wid: str) -> str:
        """Path of one window folder; refuses anything that is not a plain folder name (path traversal)."""
        if not (SAFE_NAME.match(sdir) and SAFE_NAME.match(wid)):
            raise ValueError("bad session or window name")
        return os.path.join(self.dir, sdir, "windows", wid)

    def detail(self, sdir: str, wid: str) -> dict | None:
        wdir = self.window_dir(sdir, wid)
        row = self.get(sdir, wid)
        try:
            d = json.load(open(os.path.join(wdir, "detail.json")))
        except (OSError, ValueError):
            d = self._rebuild(wdir)
        if d is None and row is None:
            return None
        d = d or {}
        win = {k: v for k, v in (row or d.get("window") or {}).items() if k not in META_KEYS}
        return {**d, "window": win, "history": {k: (row or {}).get(k) for k in META_KEYS}}

    @staticmethod
    def _rebuild(wdir: str) -> dict | None:
        """Windows scored before detail.json existed: same detail from scores.csv + subgraphs.json."""
        sp = os.path.join(wdir, "scores.csv")
        if not os.path.exists(sp):
            return None
        import pandas as pd
        nodes = pd.read_csv(sp, dtype={"node_uuid": str})
        if "attr" not in nodes:
            nodes["attr"] = ""
        nodes["flagged"] = nodes["flagged"].astype(str).str.lower() == "true"
        try:
            sg = json.load(open(os.path.join(wdir, "subgraphs.json")))
        except (OSError, ValueError):
            sg = {}
        return make_detail(nodes, sg.get("subgraphs", []), sg.get("stats", {}), sg.get("truncated", False))


# --------------------------------------------------------------------------- selftest
def selftest():
    """'It runs' + invariants: append/reload, last line wins, torn line skipped, range overlap, restart dedup,
    alert filter, backfill from windows.csv + llm_verdicts.csv, detail fallback, path-traversal guard.
    engine.py --selftest checks the live engine writes it. Not evidence that anything detects attacks."""
    import tempfile
    import pandas as pd
    base = 1_793_704_800_000                                     # 2026-11-03T11:20:00Z
    m5 = 300_000

    def win(k, status="scored", score=0.0, alert=False):
        s = base + k * m5
        return {"window": f"w{k}", "start_ms": s, "end_ms": s + m5, "start_utc": "", "status": status, "edges": 10,
                "nodes": 5, "flagged": 1, "subgraphs": 1, "subgraphs_kept": int(alert), "max_sg_score": score,
                "max_level": "Moderate" if alert else "Minor", "alert": alert}

    with tempfile.TemporaryDirectory() as d:
        # --- backfill: an old session folder with windows.csv + an LLM verdict, a replay folder, junk
        old = os.path.join(d, "live-old")
        os.makedirs(os.path.join(old, "windows", "w1"))
        json.dump({"session": "live-old", "mode": "live", "alg1": {"moderate_from": 30.0}},
                  open(os.path.join(old, "session.json"), "w"))
        cols = ["window", "start_ms", "end_ms", "start_utc", "status", "edges", "nodes", "flagged", "subgraphs",
                "subgraphs_kept", "max_sg_score", "max_level", "alert", "edges_dropped_relation", "llm"]
        with open(os.path.join(old, "windows.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerow(["w0", base, base + m5, "", "partial", 3, 0, 0, 0, 0, 0.0, "", False, "", ""])
            w.writerow(["w1", base + m5, base + 2 * m5, "", "scored", 50, 9, 4, 1, 1, 37.5, "Moderate", True, 0,
                        "pending"])
        with open(os.path.join(old, "llm_verdicts.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["window", "verdict", "confidence", "tactics", "ms", "model"])
            w.writerow(["w1", "attack", 0.85, "TA0002 TA0007", 900, "m"])
        pd.DataFrame([["p1", "process", 2.0, 1.0, 1.0, True, "c:\\x\\cmd.exe"],
                      ["n1", "net", -1.0, 0.5, -1.5, False, "-1,707,025,385:-17663"]],
                     columns=["node_uuid", "node_type", "score", "tau", "margin", "flagged", "attr"]
                     ).to_csv(os.path.join(old, "windows", "w1", "scores.csv"), index=False)
        json.dump({"subgraphs": [{"id": 0, "nodes": [{"id": "p1", "type": "process", "attr": "c:\\x\\cmd.exe"}]}],
                   "stats": {"seeds": 1}, "truncated": False}, open(os.path.join(old, "windows", "w1", "subgraphs.json"), "w"))
        os.makedirs(os.path.join(d, "replay-x"))
        open(os.path.join(d, "replay-x", "windows.csv"), "w").write(",".join(cols) + "\n" +
                                                                   f"w1,{base + m5},{base + 2 * m5},,scored,1,1,0,0,0,0,,False,,\n")
        os.makedirs(os.path.join(d, ".hidden"))                  # not a session name: skipped

        h = HistoryStore(d)
        assert h.backfilled == 3, h.backfilled
        r = h.get("live-old", "w1")
        assert r["alert"] is True and r["llm"]["status"] == "attack" and r["llm"]["confidence"] == 0.85, r
        assert r["moderate_from"] == 30.0 and r["mode"] == "live", r

        # --- live appends: a restart folder .r2 re-scores w1 and w2; later lines win
        meta = {"session": "live-old", "mode": "live", "moderate_from": 10.0}
        h.append({"sdir": "live-old.r2", **meta, **win(1, "partial")})
        h.append({"sdir": "live-old.r2", **meta, **win(2, "scoring")})
        h.append({"sdir": "live-old.r2", **meta, **win(2, "scored", 12.0, True)})
        h.append({"sdir": "live-old.r2", **meta, **win(3, "scored", 2.0)})
        h.append({"sdir": "live-old.r2", **meta, **win(2, "scored", 12.0, True), "llm": {"status": "uncertain"}})
        with open(h.path, "a") as fh:
            fh.write('{"sdir": "torn", "wind')                   # a crash mid-line

        h2 = HistoryStore(d)                                     # reload from disk = the server restarted
        assert h2.bad_lines == 1 and h2.backfilled == 0
        q = h2.query(base, base + 4 * m5)
        assert [w["window"] for w in q["windows"]] == ["w0", "w1", "w2", "w3"], q
        w1 = q["windows"][1]
        assert w1["sdir"] == "live-old" and w1["status"] == "scored", "a scored window beats the restart's partial"
        assert q["duplicates_hidden"] == 1
        assert q["windows"][2]["llm"]["status"] == "uncertain", "last line per window wins"
        # range = overlap with [from, to): a 1-minute range inside w2 returns w2 only
        assert [w["window"] for w in h2.query(base + 2 * m5 + 60_000, base + 2 * m5 + 120_000)["windows"]] == ["w2"]
        assert [w["window"] for w in h2.query(base, base + 4 * m5, alert_only=True)["windows"]] == ["w1", "w2"]
        assert h2.query(base, base + 4 * m5, include_replay=True)["duplicates_hidden"] == 2   # + the replay's w1
        assert h2.query(base, base + 4 * m5, limit=2)["windows"][0]["window"] == "w2"   # newest kept

        # --- detail: no detail.json -> rebuilt from scores.csv + subgraphs.json (display attrs decoded)
        det = h2.detail("live-old", "w1")
        assert det["window"]["max_sg_score"] == 37.5 and det["history"]["sdir"] == "live-old"
        assert det["top"]["process"][0]["flagged"] is True
        assert det["top"]["net"][0]["attr"] == "23.220.64.154:443", det["top"]["net"]
        write_json_atomic(os.path.join(old, "windows", "w1", "detail.json"),
                          {"window": {"window": "w1"}, "top": {}, "llm": {"verdict": "attack"}})
        assert h2.detail("live-old", "w1")["llm"]["verdict"] == "attack"
        assert h2.detail("live-old", "nope") is None
        for bad in (("..", "w1"), ("live-old", "../../etc"), ("a/b", "w1"), (".hidden", "w1")):
            try:
                h2.detail(*bad)
                raise AssertionError(f"path accepted: {bad}")
            except ValueError:
                pass

        # --- two hosts on one window grid: both rows stay, and host= picks one
        for hname, sd in (("ws-etw-0", "live-h-ws-etw-0"), ("ws-etw-1", "live-h-ws-etw-1")):
            h2.append({"sdir": sd, **meta, "host": hname, **win(9, "scored", 1.0)})
        q2 = h2.query(base + 9 * m5, base + 10 * m5)
        assert [w["host"] for w in q2["windows"]] == ["ws-etw-0", "ws-etw-1"], q2
        assert [w["sdir"] for w in h2.query(base + 9 * m5, base + 10 * m5, host="ws-etw-1")["windows"]] == ["live-h-ws-etw-1"]
        assert h2.query(base, base + 4 * m5, host="ws-etw-1")["windows"] == [], "old rows have no host"

        assert parse_time_ms("2026-11-03T11:20:00Z") == base == parse_time_ms(str(base))
        assert parse_time_ms("2026-11-03T18:20:00+07:00") == base == parse_time_ms("2026-11-03T11:20:00")
    print("[SELFTEST OK] backfill from windows.csv + llm_verdicts.csv, append/reload, torn line skipped, last line "
          "wins, restart dedup (scored beats partial), range overlap, alerts-only, replays left out, limit, detail from "
          "detail.json or rebuilt, path traversal refused, time parsing, two hosts on one window grid")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
