#!/usr/bin/env python3
"""
file_store.py - the file implementations of SessionStore and CrosshostRepository (repository.py): the sessions/
folder layout the live server has always written. Moved here from engine.py / correlate.py by the repository
refactor; the bytes on disk are unchanged.

    sessions/<sdir>/session.json            run parameters, written once (+ restart_of for a .rN restart folder)
    sessions/<sdir>/windows.csv             one row per closed window
    sessions/<sdir>/raw/<stream>.json       verbatim raw lines (replay / re-judge)
    sessions/<sdir>/windows/<wid>/*         edges, attrs, features, scores, subgraphs, detail.json, llm/
    sessions/<sdir>/llm_verdicts.csv        one row per LLM verdict
    sessions/llm_budget.json                {"date", "used"}: red windows investigated today (all engines share it)
    sessions/crosshost.jsonl                cross-host findings, last line per id wins

Usage (from the repo root)
    uv run python src/live/file_store.py --selftest
"""
from __future__ import annotations

import csv
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from history import write_json_atomic  # noqa: E402
from repository import CrosshostRepository, SessionStore, check_name  # noqa: E402

# one llm_budget.json, shared by every host's engine and the correlator: a process-wide lock, not per store object
BUDGET_LOCK = threading.Lock()
VERDICT_HEADER = ["window", "verdict", "confidence", "tactics", "ms", "model"]


class FileSessionStore(SessionStore):
    def __init__(self, sessions_dir: str):
        self.root = sessions_dir
        self.budget_path = os.path.join(sessions_dir, "llm_budget.json")
        os.makedirs(sessions_dir, exist_ok=True)

    def path(self, sdir: str = "") -> str:
        if sdir:
            check_name(sdir)
        return os.path.join(self.root, sdir) if sdir else self.root

    # ---------------------------------------------------------------- session
    def create_session(self, name: str, meta: dict, index_header: list[str]) -> str:
        check_name(name)
        # a restart mid-run re-reads the run from Loki: a fresh folder <name>.r2, .r3, ... instead of appending a
        # second copy of the raw lines to the old one (that would corrupt replay / re-judge of the session)
        sdir, n = name, 1
        while os.path.exists(os.path.join(self.root, sdir, "session.json")):
            n += 1
            sdir = f"{name}.r{n}"
        p = self.path(sdir)
        os.makedirs(os.path.join(p, "raw"), exist_ok=True)
        os.makedirs(os.path.join(p, "windows"), exist_ok=True)
        json.dump({**meta, "restart_of": name if sdir != name else None},
                  open(os.path.join(p, "session.json"), "w"), indent=2)
        with open(os.path.join(p, "windows.csv"), "w", newline="") as fh:
            csv.writer(fh).writerow(index_header)
        return sdir

    def open_raw(self, sdir: str, stream: str):
        check_name(stream)
        return open(os.path.join(self.path(sdir), "raw", stream + ".json"), "a", encoding="utf-8")

    # ---------------------------------------------------------------- windows
    def window_dir(self, sdir: str, wid: str) -> str:
        check_name(wid)
        d = os.path.join(self.path(sdir), "windows", wid)
        os.makedirs(d, exist_ok=True)
        return d

    def write_window(self, sdir: str, wid: str, files: dict) -> None:
        import pandas as pd
        d = self.window_dir(sdir, wid)
        for name, v in files.items():
            check_name(name)
            p = os.path.join(d, name)
            if callable(v):
                v(p)
            elif isinstance(v, pd.DataFrame):
                v.to_csv(p, index=False)
            elif isinstance(v, tuple):                    # (DataFrame, to_csv kwargs)
                v[0].to_csv(p, **v[1])
            else:
                json.dump(v, open(p, "w"), indent=1)

    def append_index(self, sdir: str, row: list) -> None:
        with open(os.path.join(self.path(sdir), "windows.csv"), "a", newline="") as fh:
            csv.writer(fh).writerow(row)

    def append_verdict(self, sdir: str, row: list) -> None:
        with open(os.path.join(self.path(sdir), "llm_verdicts.csv"), "a", newline="") as fh:
            w = csv.writer(fh)
            if fh.tell() == 0:
                w.writerow(VERDICT_HEADER)
            w.writerow(row)

    def save_detail(self, sdir: str, wid: str, detail: dict) -> None:
        write_json_atomic(os.path.join(self.window_dir(sdir, wid), "detail.json"), detail)

    def load_detail(self, sdir: str, wid: str) -> dict | None:
        check_name(wid)
        try:
            return json.load(open(os.path.join(self.path(sdir), "windows", wid, "detail.json")))
        except (OSError, ValueError):
            return None

    # ---------------------------------------------------------------- LLM budget
    def budget(self, today: str) -> dict:
        try:
            b = json.load(open(self.budget_path))
        except (OSError, ValueError):
            b = {}
        return b if b.get("date") == today else {"date": today, "used": 0}

    def take_budget(self, limit: int, today: str) -> bool:
        with BUDGET_LOCK:
            b = self.budget(today)
            if limit and b["used"] >= limit:
                return False
            b["used"] += 1
            os.makedirs(self.root, exist_ok=True)
            tmp = self.budget_path + ".tmp"
            json.dump(b, open(tmp, "w"))
            os.replace(tmp, self.budget_path)     # atomic: a crash never leaves half a file
            return True


class FileCrosshostRepository(CrosshostRepository):
    def __init__(self, sessions_dir: str):
        self.path = os.path.join(sessions_dir, "crosshost.jsonl")
        os.makedirs(sessions_dir, exist_ok=True)

    def load(self) -> dict[str, dict]:
        out = {}
        if not os.path.exists(self.path):
            return out
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    f = json.loads(line)
                    out[f["id"]] = f
                except (ValueError, KeyError):
                    pass                              # a line torn by a crash
        return out

    def append(self, finding: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(finding) + "\n")


# --------------------------------------------------------------------------- selftest
def selftest():
    """'It runs': restart folders, file layout, budget per day + limit, crosshost last-wins, name guard."""
    import tempfile
    import pandas as pd
    with tempfile.TemporaryDirectory() as d:
        st = FileSessionStore(d)
        hdr = ["window", "status"]
        a = st.create_session("live-x", {"session": "live-x"}, hdr)
        b = st.create_session("live-x", {"session": "live-x"}, hdr)
        assert (a, b) == ("live-x", "live-x.r2"), (a, b)
        assert json.load(open(os.path.join(d, b, "session.json")))["restart_of"] == "live-x"
        assert json.load(open(os.path.join(d, a, "session.json")))["restart_of"] is None
        with st.open_raw(a, "kproc") as fh:
            fh.write("line\n")
        st.write_window(a, "w1", {"edges.csv": pd.DataFrame({"x": [1]}),
                                  "f.csv": (pd.DataFrame({"y": [0.5]}, index=["n"]), {"index_label": "node_uuid"}),
                                  "sg.json": {"subgraphs": []},
                                  "c.csv": lambda p: open(p, "w").write("node\n")})
        assert open(os.path.join(d, a, "windows", "w1", "f.csv")).read().startswith("node_uuid,y")
        st.append_index(a, ["w1", "scored"])
        assert open(os.path.join(d, a, "windows.csv")).read().splitlines() == ["window,status", "w1,scored"]
        st.append_verdict(a, ["w1", "attack", 0.9, "TA0002", 10, "m"])
        st.append_verdict(a, ["w2", "skipped", "", "", "", "m"])
        assert len(open(os.path.join(d, a, "llm_verdicts.csv")).read().splitlines()) == 3
        st.save_detail(a, "w1", {"top": {}})
        assert st.load_detail(a, "w1") == {"top": {}} and st.load_detail(a, "w9") is None
        assert st.take_budget(2, "d1") and st.take_budget(2, "d1") and not st.take_budget(2, "d1")
        assert st.budget("d1")["used"] == 2 and st.budget("d2")["used"] == 0 and st.take_budget(2, "d2")
        assert st.take_budget(0, "d2") and st.budget("d2")["used"] == 2, "limit 0 = no limit"
        for bad in ("../x", "a/b"):
            try:
                st.window_dir(a, bad)
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass
        cr = FileCrosshostRepository(d)
        cr.append({"id": "f1", "level": "pending"})
        cr.append({"id": "f1", "level": "corroborated"})
        with open(cr.path, "a") as fh:
            fh.write('{"id": "torn')
        assert cr.load() == {"f1": {"id": "f1", "level": "corroborated"}}
    print("[file_store] selftest OK (restart folders, window files, index/verdicts, detail, budget, crosshost, names)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
