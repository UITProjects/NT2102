#!/usr/bin/env python3
"""
engine.py - live windowed OCR-APT scoring, no web code (live_server.py is the thin HTTP layer).

    shipper batches (raw SilkETW JSON lines + byte offsets + VM clock)
      -> ingest(): idempotent by offset, raw lines appended to sessions/<id>/raw/<stream>.json
      -> reorder heap keyed (ts, stream, offset)        same tie order as etw2nodlink's heapq.merge
      -> released when ts < watermark = clock_ms - lateness
      -> LiveGraph = etw2nodlink.Graph (the SAME converter code that built the training edges,
                     loaded from scripts/04-collector.ps1) + the shipper's own PID excluded
      -> edge buffer (sorted by ts)
      -> window [start, start + w) closes when watermark >= end
      -> scoring thread: score_window.score_edges (train scaler + train relations) -> alg1_lite
      -> sessions/<id>/windows/<wid>/*.csv|json  + listeners (SSE in live_server.py)

Time: only the shipper's clock is used (clock_ms), never this host's wall clock, so a replay at
60x gives exactly the same windows as the live capture would.

Usage
    python engine.py --selftest
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import heapq
import json
import math
import os
import queue
import re
import sys
import threading
import time
import types
from collections import Counter, deque
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SRC, "pipeline"))
import score_window as sw  # noqa: E402

EDGE_HEADER = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
DEFAULT_PS1 = os.path.join(SRC, "scripts", "04-collector.ps1")
MAX_FLAGGED_FOR_SUBGRAPHS = 300   # a broken model flags everything; keep the UI usable


# --------------------------------------------------------------------------- converter reuse
def load_converter(ps1_path: str = DEFAULT_PS1):
    """Extract etw2nodlink.py from the $converter here-string in 04-collector.ps1 and import it.

    The provisioner writes the here-string with LF endings and no trailing newline, UTF-8 without
    BOM, so the sha256 of exactly that text equals `converter_sha256` / experiment.json tools.converter
    when the VM ran the same version."""
    text = open(ps1_path, encoding="utf-8").read().replace("\r\n", "\n")
    m = re.search(r"^\$converter = @'\n(.*?)\n'@$", text, re.S | re.M)
    if not m:
        raise SystemExit(f"[engine] no $converter here-string in {ps1_path}")
    code = m.group(1)
    mod = types.ModuleType("etw2nodlink")
    mod.__file__ = ps1_path + "::$converter"
    exec(compile(code, mod.__file__, "exec"), mod.__dict__)
    return mod, hashlib.sha256(code.encode("utf-8")).hexdigest()


class EdgeSink:
    """Stands in for the csv.writer that etw2nodlink.Graph writes edge rows to."""

    def __init__(self):
        self.rows = []      # [src, src_type, dst, dst_type, edge_type, ts_ms], appended in ts order

    def writerow(self, row):
        self.rows.append(row)


def make_live_graph(conv):
    class LiveGraph(conv.Graph):
        """etw2nodlink.Graph + drop edges of excluded PIDs (the shipper itself).

        Process node ids are "p<pid>_<start_ms>" or "p<pid>_pre_<run>", so the PID is recoverable.
        Without this, the shipper (reads the SilkETW files every second, POSTs to the host) is a new,
        busy process never seen in training -> it would be flagged in every window."""

        def __init__(self, writer, dmap, stats, exclude_pids):
            super().__init__(writer, dmap, stats)
            self.exclude_pids = exclude_pids

        def edge(self, proc_id, other_id, etype, t):
            try:
                pid = int(proc_id[1:].split("_", 1)[0])
            except ValueError:
                pid = None
            if pid in self.exclude_pids:
                self.st["excluded"]["shipper_" + etype] += 1
                return
            super().edge(proc_id, other_id, etype, t)

    return LiveGraph


# --------------------------------------------------------------------------- Alg. 1 (simplified)
def alg1_lite(nodes: pd.DataFrame, edges: pd.DataFrame, attrs: dict) -> tuple[list, bool]:
    """[OURS] simplified OCR-APT Alg. 1 on ONE window graph.

    A       = flagged nodes (score > tau of their type)
    bridges = unflagged nodes adjacent to >= 2 distinct flagged nodes (they connect the story)
    subgraphs = connected components of A + bridges, over edges with >= 1 flagged endpoint.
    NOT done (vs paper §5.2): no subgraph anomaly-score filter ("Without SG-Det", Table 4), no
    multi-hop expansion. Expect more, noisier subgraphs than the paper's.
    """
    flagged = nodes[nodes["flagged"]].sort_values("margin", ascending=False)
    truncated = len(flagged) > MAX_FLAGGED_FOR_SUBGRAPHS
    flagged = flagged.head(MAX_FLAGGED_FOR_SUBGRAPHS)
    A = set(flagged["node_uuid"])
    if not A:
        return [], False
    known = set(nodes["node_uuid"])                    # edges dropped by the relation filter are not in the graph
    edges = edges[edges["source-id"].isin(known) & edges["destination-id"].isin(known)]
    s, d = edges["source-id"].to_numpy(), edges["destination-id"].to_numpy()
    nbr_flagged = {}                                   # unflagged node -> set of flagged neighbours
    for a, b in zip(s, d):
        if a == b:
            continue
        if a in A and b not in A:
            nbr_flagged.setdefault(b, set()).add(a)
        elif b in A and a not in A:
            nbr_flagged.setdefault(a, set()).add(b)
    bridges = {n for n, fs in nbr_flagged.items() if len(fs) >= 2}
    keep = A | bridges

    parent = {n: n for n in keep}                     # union-find

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    mask = [(a in keep and b in keep and (a in A or b in A)) for a, b in zip(s, d)]
    sub = edges[mask]
    for a, b in zip(sub["source-id"], sub["destination-id"]):
        parent[find(a)] = find(b)

    info = nodes.set_index("node_uuid")
    comps = {}
    for n in keep:
        comps.setdefault(find(n), []).append(n)
    out = []
    for members in comps.values():
        mset = set(members)
        if not (mset & A):
            continue
        e = sub[sub["source-id"].isin(mset) & sub["destination-id"].isin(mset)]
        agg = (e.groupby(["source-id", "destination-id", "edge-type"])["timestamp"]
               .agg(["size", "min", "max"]).reset_index()) if len(e) else pd.DataFrame()
        nlist = []
        for n in members:
            r = info.loc[n]
            nlist.append({"id": n, "type": r["node_type"], "attr": attrs.get(n, ""),
                          "score": _f(r["score"]), "tau": _f(r["tau"]), "margin": _f(r["margin"]),
                          "role": "anomalous" if n in A else "bridge"})
        nlist.sort(key=lambda x: (x["role"] != "anomalous", -(x["margin"] or 0)))
        elist = [{"src": r["source-id"], "dst": r["destination-id"], "type": r["edge-type"],
                  "count": int(r["size"]), "first_ms": int(r["min"]), "last_ms": int(r["max"])}
                 for _, r in agg.iterrows()] if len(agg) else []
        elist.sort(key=lambda x: x["first_ms"])
        out.append({"n_anomalous": len(mset & A), "n_bridges": len(mset & bridges),
                    "max_margin": max(x["margin"] for x in nlist if x["role"] == "anomalous"),
                    "nodes": nlist, "edges": elist})
    out.sort(key=lambda g: (-g["n_anomalous"], -g["max_margin"]))
    return out, truncated


def _f(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def utc_id(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- engine
class Engine:
    def __init__(self, run_dir, scaler_path, sessions_dir, window_min=5, stride_min=None, lateness_s=20,
                 origin_ms=0, ps1_path=DEFAULT_PS1, experiment_json=None, allow_collapsed=False, top_k=20):
        self.conv, self.conv_sha = load_converter(ps1_path)
        self.conv_check = "not checked (no --experiment)"
        if experiment_json:
            want = json.load(open(experiment_json)).get("tools", {}).get("converter", "")
            self.conv_check = "match" if want.lower() == self.conv_sha else f"MISMATCH (training used {want})"
            if want.lower() != self.conv_sha:
                print(f"[engine] WARNING converter sha256 {self.conv_sha} != training {want}: live edges are "
                      f"built by different converter code than the training edges")
        self.LiveGraph = make_live_graph(self.conv)
        self.scaler = sw.load_scaler(scaler_path)
        self.run = sw.load_run(run_dir, self.scaler, allow_collapsed)
        self.run_dir, self.scaler_path = os.path.abspath(run_dir), os.path.abspath(scaler_path)
        self.sessions_dir = sessions_dir
        self.w_ms = int(window_min * 60_000)
        self.s_ms = int((stride_min or window_min) * 60_000)
        self.lateness_ms = int(lateness_s * 1000)
        self.origin_ms = int(origin_ms)
        self.top_k = top_k

        self.lock = threading.RLock()
        self.listeners = []                       # callables(event_dict), called from any thread
        self.jobs = queue.Queue()
        self.session = None
        threading.Thread(target=self._scoring_loop, daemon=True, name="scoring").start()

    # ---------------------------------------------------------------- session
    def _new_session(self, sid, payload):
        sdir = os.path.join(self.sessions_dir, re.sub(r"[^A-Za-z0-9._-]", "_", sid))
        os.makedirs(os.path.join(sdir, "raw"), exist_ok=True)
        os.makedirs(os.path.join(sdir, "windows"), exist_ok=True)
        dmap = {str(k).lower(): str(v).lower() for k, v in (payload.get("dos_map") or {}).items()}
        dmap = dict(sorted(dmap.items(), key=lambda kv: -len(kv[0])))   # longest device prefix first
        stats = {k: Counter() for k in ("events", "edges", "excluded", "unresolved", "unmapped", "bad_lines")}
        sink = EdgeSink()
        exclude = set(int(p) for p in (payload.get("exclude_pids") or [payload.get("pid")]) if p is not None)
        g = self.LiveGraph(sink, dmap, stats, exclude)
        # run name goes into ids of processes started before the capture ("p<pid>_pre_<run>"); the
        # shipper sends the capture folder name, as etw2nodlink.convert uses, so replayed ids match training
        g.new_run(str(payload.get("run") or sid))
        self.session = {
            "id": sid, "dir": sdir, "mode": payload.get("mode", "live"), "host": payload.get("host", ""),
            "dmap": dmap, "exclude_pids": sorted(exclude), "graph": g, "sink": sink, "stats": stats,
            "offsets": {}, "heap": [], "released_wm": None, "first_t": None, "max_event_t": None,
            "clock_ms": None, "last_seen": None, "late_dropped": 0, "next_k": None, "edge_base": 0,
            "windows": [], "by_id": {}, "details": {}, "rate": deque(), "lines_total": Counter(),
            "raw_fh": {}, "started": time.time(),
        }
        meta = {"session": sid, "mode": self.session["mode"], "host": self.session["host"],
                "dos_map": dmap, "exclude_pids": sorted(exclude), "run_dir": self.run_dir,
                "scaler": self.scaler_path, "converter_sha256": self.conv_sha, "converter_check": self.conv_check,
                "window_min": self.w_ms / 60000, "stride_min": self.s_ms / 60000,
                "lateness_s": self.lateness_ms / 1000, "origin_ms": self.origin_ms,
                "tau": self.run.tau, "created_utc": utc_iso(int(time.time() * 1000))}
        json.dump(meta, open(os.path.join(sdir, "session.json"), "w"), indent=2)
        with open(os.path.join(sdir, "windows.csv"), "w", newline="") as fh:
            csv.writer(fh).writerow(["window", "start_ms", "end_ms", "start_utc", "status", "edges", "nodes",
                                     "flagged", "subgraphs", "edges_dropped_relation"])
        print(f"[engine] new session {sid} ({self.session['mode']}) -> {sdir}  dos_map={dmap}  exclude={exclude}")
        self._emit({"type": "session", "session": sid})

    # ---------------------------------------------------------------- ingest
    def ingest(self, payload: dict) -> tuple[int, dict]:
        """Returns (http_status, body). 409 = offset gap: body.expected tells the shipper where to resume."""
        with self.lock:
            sid = str(payload.get("session") or "")
            if not sid:
                return 400, {"error": "missing session"}
            if self.session is None or self.session["id"] != sid:
                if self.session is not None:
                    self._close_raw()
                self._new_session(sid, payload)
            S = self.session
            S["last_seen"] = time.time()
            accepted = dup = 0
            for b in payload.get("batches") or []:
                stream = re.sub(r"[^A-Za-z0-9_-]", "_", str(b["stream"]))
                exp = S["offsets"].get(stream, 0)
                start = int(b["offset_start"])
                if start > exp:
                    return 409, {"error": "offset gap", "expected": {stream: exp}}
                pos = start
                for line, end in zip(b["lines"], b["ends"]):
                    end = int(end)
                    if end <= exp:                       # already have it (retry after a lost response)
                        pos, dup = end, dup + 1
                        continue
                    self._accept_line(S, stream, line, end)
                    pos, exp, accepted = end, end, accepted + 1
                S["offsets"][stream] = exp
            clock = payload.get("clock_ms")
            if clock is not None:
                S["clock_ms"] = int(clock)
                self._advance(S, int(clock) - self.lateness_ms)
            for fh in S["raw_fh"].values():         # raw copy survives a server crash
                fh.flush()
            now = time.time()
            S["rate"].append((now, accepted))
            while S["rate"] and S["rate"][0][0] < now - 10:
                S["rate"].popleft()
            return 200, {"accepted": accepted, "duplicates": dup, "offsets": S["offsets"]}

    def _accept_line(self, S, stream, line, end):
        fh = S["raw_fh"].get(stream)
        if fh is None:
            fh = S["raw_fh"][stream] = open(os.path.join(S["dir"], "raw", stream + ".json"), "a", encoding="utf-8")
        fh.write(line + "\n")
        S["lines_total"][stream] += 1
        try:
            ev = json.loads(line.strip().lstrip("\ufeff"))
            t = self.conv.ts_ms(self.conv.lower_keys(ev).get("timestamp"))
        except (ValueError, TypeError, AttributeError):
            S["stats"]["bad_lines"][stream] += 1
            return
        if S["released_wm"] is not None and t < S["released_wm"]:
            S["late_dropped"] += 1                     # its window may already be scored
            return
        S["max_event_t"] = t if S["max_event_t"] is None else max(S["max_event_t"], t)
        # tie order = etw2nodlink.convert: heapq.merge over sorted file names, then file order
        heapq.heappush(S["heap"], (t, stream, end, ev))

    def _advance(self, S, wm):
        if S["released_wm"] is not None and wm <= S["released_wm"]:
            return
        g, heap = S["graph"], S["heap"]
        while heap and heap[0][0] < wm:
            t, _, _, ev = heapq.heappop(heap)
            if S["first_t"] is None:
                S["first_t"] = t
                # first window whose end is after the first event
                S["next_k"] = (t - self.origin_ms - self.w_ms) // self.s_ms + 1
            g.handle(t, ev)
        S["released_wm"] = wm
        self._close_windows(S, wm)

    def _close_windows(self, S, wm):
        if S["next_k"] is None:
            return
        rows = S["sink"].rows
        while True:
            k = S["next_k"]
            start = self.origin_ms + k * self.s_ms
            end = start + self.w_ms
            if end > wm:
                break
            ts = [r[5] for r in rows]          # rows are in ts order (events released in ts order)
            lo, hi = bisect.bisect_left(ts, start), bisect.bisect_left(ts, end)
            w_rows = rows[lo:hi]
            wid = utc_id(start)
            rec = {"window": wid, "start_ms": start, "end_ms": end, "start_utc": utc_iso(start),
                   "edges": len(w_rows), "edges_by_type": dict(Counter(r[4] for r in w_rows)),
                   "status": "partial" if start < S["first_t"] else ("empty" if not w_rows else "scoring"),
                   "nodes": 0, "flagged": 0, "flagged_by_type": {}, "subgraphs": 0, "alert": False}
            S["windows"].append(rec)
            S["by_id"][wid] = rec
            if rec["status"] == "scoring":
                attrs = {}
                for r in w_rows:
                    for n in (r[0], r[2]):
                        if n not in attrs:
                            attrs[n] = S["graph"].nodes[n][1] or "unknown"
                self.jobs.put((S["id"], wid, list(w_rows), attrs))
            else:
                self._write_index(S, rec, {})
            self._emit({"type": "window", "window": rec})
            S["next_k"] = k + 1
            # drop edges no future window needs
            keep_from = self.origin_ms + S["next_k"] * self.s_ms
            cut = bisect.bisect_left(ts, keep_from)
            if cut:
                del rows[:cut]

    # ---------------------------------------------------------------- scoring
    def _scoring_loop(self):
        while True:
            sid, wid, w_rows, attrs = self.jobs.get()
            try:
                self._score(sid, wid, w_rows, attrs)
            except Exception as e:  # keep the loop alive; show the error on the window
                import traceback
                traceback.print_exc()
                with self.lock:
                    if self.session and self.session["id"] == sid:
                        rec = self.session["by_id"][wid]
                        rec.update(status="error", error=f"{type(e).__name__}: {e}")
                        self._emit({"type": "window", "window": rec})
            finally:
                self.jobs.task_done()

    def _score(self, sid, wid, w_rows, attrs):
        t0 = time.time()
        edges = pd.DataFrame(w_rows, columns=EDGE_HEADER)
        nodes, feats, info = sw.score_edges(edges, self.run, self.scaler)
        subgraphs, truncated = alg1_lite(nodes, sw.tgm.normalise_edges(edges), attrs)
        nodes["attr"] = nodes["node_uuid"].map(attrs)
        ms = int((time.time() - t0) * 1000)

        with self.lock:
            S = self.session
            if S is None or S["id"] != sid:
                return
            wdir = os.path.join(S["dir"], "windows", wid)
            os.makedirs(wdir, exist_ok=True)
            edges.to_csv(os.path.join(wdir, "edges.csv"), index=False)
            ntype = dict(zip(edges["source-id"], edges["source-type"]))
            ntype.update(zip(edges["destination-id"], edges["destination-type"]))
            pd.DataFrame([(n, ntype.get(n, ""), a) for n, a in attrs.items()],
                         columns=["node", "node_type", "node_attr"]).to_csv(
                os.path.join(wdir, "node_attrs.csv"), index=False)
            feats.to_csv(os.path.join(wdir, "features_model.csv"), index_label="node_uuid", float_format="%.6f")
            nodes.sort_values("score", ascending=False).to_csv(os.path.join(wdir, "scores.csv"), index=False)
            json.dump({"window": wid, "truncated": truncated, "subgraphs": subgraphs},
                      open(os.path.join(wdir, "subgraphs.json"), "w"), indent=1)

            top = {}
            for t, g in nodes.groupby("node_type"):
                g = g.sort_values("margin", ascending=False).head(self.top_k)
                top[t] = [{"id": r.node_uuid, "attr": r.attr, "score": _f(r.score), "tau": _f(r.tau),
                           "margin": _f(r.margin), "flagged": bool(r.flagged)} for r in g.itertuples()]
            rec = S["by_id"][wid]
            fl = nodes[nodes["flagged"]]
            rec.update(status="scored", nodes=int(len(nodes)), flagged=int(len(fl)),
                       nodes_by_type=nodes["node_type"].value_counts().to_dict(),
                       flagged_by_type=fl["node_type"].value_counts().to_dict(),
                       subgraphs=len(subgraphs), alert=bool(len(fl)), scoring_ms=ms,
                       edges_dropped_relation=info["edges_dropped_relation"],
                       max_margin=_f(nodes["margin"].max()))
            S["details"][wid] = {"top": top, "subgraphs": subgraphs[:25], "truncated": truncated,
                                 "subgraphs_total": len(subgraphs)}
            self._write_index(S, rec, info)
            self._emit({"type": "window", "window": rec})

    def _write_index(self, S, rec, info):
        with open(os.path.join(S["dir"], "windows.csv"), "a", newline="") as fh:
            csv.writer(fh).writerow([rec["window"], rec["start_ms"], rec["end_ms"], rec["start_utc"], rec["status"],
                                     rec["edges"], rec["nodes"], rec["flagged"], rec["subgraphs"],
                                     info.get("edges_dropped_relation", "")])

    # ---------------------------------------------------------------- read side
    def status(self) -> dict:
        with self.lock:
            base = {"model": {"run_dir": self.run_dir, "tau": self.run.tau, "scaler": self.scaler_path,
                              "converter_sha256": self.conv_sha, "converter_check": self.conv_check},
                    "params": {"window_min": self.w_ms / 60000, "stride_min": self.s_ms / 60000,
                               "lateness_s": self.lateness_ms / 1000, "origin_ms": self.origin_ms},
                    "server_ms": int(time.time() * 1000), "session": None}
            S = self.session
            if S is None:
                return base
            open_win = None
            if S["next_k"] is not None:
                st = self.origin_ms + S["next_k"] * self.s_ms
                open_win = {"start_ms": st, "end_ms": st + self.w_ms}
            span = (S["rate"][-1][0] - S["rate"][0][0]) if len(S["rate"]) > 1 else 0
            st = S["stats"]
            base["session"] = {
                "id": S["id"], "mode": S["mode"], "host": S["host"], "dir": S["dir"],
                "last_seen_s_ago": round(time.time() - S["last_seen"], 1) if S["last_seen"] else None,
                "clock_ms": S["clock_ms"], "watermark_ms": S["released_wm"], "max_event_ms": S["max_event_t"],
                # VM clock vs event timestamps: large values mean a timezone / clock problem
                "event_lag_s": round((S["clock_ms"] - S["max_event_t"]) / 1000, 1)
                if S["clock_ms"] and S["max_event_t"] else None,
                "clock_skew_s": round((time.time() * 1000 - S["clock_ms"]) / 1000, 1)
                if S["clock_ms"] and S["mode"] == "live" else None,
                "lines_per_s": round(sum(n for _, n in S["rate"]) / span, 1) if span else 0,
                "lines_total": dict(S["lines_total"]), "heap": len(S["heap"]),
                "late_dropped": S["late_dropped"], "bad_lines": sum(st["bad_lines"].values()),
                "events": dict(st["events"]), "edges": dict(st["edges"]), "excluded": dict(st["excluded"]),
                "unresolved": dict(st["unresolved"]), "unmapped": sum(st["unmapped"].values()),
                "exclude_pids": S["exclude_pids"], "dos_map": S["dmap"],
                "open_window": open_win, "windows": len(S["windows"]),
                "scoring_queue": self.jobs.qsize()}
            return base

    def windows(self) -> list:
        with self.lock:
            return [dict(w) for w in self.session["windows"]] if self.session else []

    def window_detail(self, wid) -> dict | None:
        with self.lock:
            if not self.session or wid not in self.session["by_id"]:
                return None
            return {"window": dict(self.session["by_id"][wid]), **self.session["details"].get(wid, {})}

    def drain(self):
        """Wait until every closed window is scored (selftest / replay)."""
        self.jobs.join()

    def _emit(self, ev):
        for cb in list(self.listeners):
            try:
                cb(ev)
            except Exception:
                pass

    def _close_raw(self):
        for fh in self.session["raw_fh"].values():
            fh.close()

    def flush_raw(self):
        with self.lock:
            if self.session:
                for fh in self.session["raw_fh"].values():
                    fh.flush()


# --------------------------------------------------------------------------- selftest
def selftest():
    """'It runs' + invariants: offsets/dedup, out-of-order streams, PID exclusion, window boundaries,
    partial window skipped, files written. Uses a tiny model trained on synthetic data.
    Not evidence that anything detects attacks."""
    import tempfile
    import encode_to_features as enc
    tgm = sw.tgm

    kp, kf, kn = ("Microsoft-Windows-Kernel-" + x for x in ("Process", "File", "Network"))
    base = 1_791_000_000_000                     # multiple of 5 min -> windows [base + 5k min, ...)
    t0 = base + 60_000                           # capture starts 1 min into a window -> it is partial

    def iso(ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "0+00:00"

    def ev(prov, name, pid, pname, ms, **data):
        return json.dumps({"ProviderName": prov, "EventName": name, "ProcessID": pid, "ProcessName": pname,
                           "TimeStamp": iso(ms), "XmlEventData": data})

    streams = {"kproc": [], "kfile": [], "knet": []}
    vol = r"\Device\HarddiskVolume4"
    streams["kproc"].append(ev(kp, "ProcessStart/Start", 100, "explorer", t0, ProcessID="200",
                               ParentProcessID="100", ImageName=vol + r"\Windows\System32\cmd.exe"))
    for i in range(16 * 60 // 5):                # 16 minutes, one burst every 5 s
        t = t0 + 1000 + i * 5000
        streams["kproc"].append(ev(kp, "ImageLoad", 200, "cmd", t, ProcessID="200",
                                   ImageName=vol + rf"\Windows\System32\lib{i % 7}.dll"))
        streams["kfile"].append(ev(kf, "Create", 200, "cmd", t + 1, FileObject=f"0x{i:x}",
                                   FileName=rf"C:\tmp\f{i % 9}.txt"))
        streams["kfile"].append(ev(kf, "Write", 200, "cmd", t + 2, FileObject=f"0x{i:x}"))
        streams["kfile"].append(ev(kf, "Read", 200, "cmd", t + 3, FileObject=f"0x{i:x}"))
        # the shipper (pid 999) reads the capture files and talks to the host: must be excluded
        streams["kfile"].append(ev(kf, "Create", 999, "python", t + 4, FileObject=f"0xs{i:x}",
                                   FileName=r"C:\lab-out\etw\raw\kproc.json"))
        streams["kfile"].append(ev(kf, "Read", 999, "python", t + 5, FileObject=f"0xs{i:x}"))
        streams["knet"].append(ev(kn, "KERNEL_NETWORK_TASK_TCPIP/Datasent.", 4, "System", t + 6,
                                  PID="200", daddr="93.184.216.34", dport="443"))
        streams["knet"].append(ev(kn, "KERNEL_NETWORK_TASK_TCPIP/Datasent.", 4, "System", t + 7,
                                  PID="999", daddr="10.21.2.1", dport="8000"))

    def batches(stream, lines, start_line, n, offset):
        """lines[start_line:start_line+n] as one shipper batch with CRLF byte offsets."""
        ends, pos = [], offset
        for ln in lines[start_line:start_line + n]:
            pos += len(ln.encode()) + 2
            ends.append(pos)
        return {"stream": stream, "offset_start": offset, "lines": lines[start_line:start_line + n], "ends": ends}, pos

    with tempfile.TemporaryDirectory() as d:
        # tiny model on synthetic windowed edges (same vocabulary as the live converter output)
        e = sw.synthetic_edges()
        e.to_csv(f"{d}/edges.csv", index=False)
        _, feats, sc = enc.build_features(enc.normalise_edges(e))
        feats.to_csv(f"{d}/features_model.csv", index_label="node_uuid")
        json.dump({"feature_order": enc.ACTION_COLS + enc.IDLE_COLS, "idle_minmax": sc, "ts_unit": "ms",
                   "repo_rounding": True, "refit": True}, open(f"{d}/scaler.json", "w"))
        tgm.run(tgm.parse_args(["--edges", f"{d}/edges.csv", "--features", f"{d}/features_model.csv",
                                "--scaler", f"{d}/scaler.json", "--out-dir", f"{d}/run",
                                "--epochs", "8", "--runs", "1", "--purge-windows", "1"]))

        eng = Engine(f"{d}/run", f"{d}/scaler.json", f"{d}/sessions", window_min=5, lateness_s=20)
        seen = []
        eng.listeners.append(seen.append)
        hello = {"session": "selftest", "mode": "replay", "pid": 999,
                 "dos_map": {vol: "c:"}, "clock_ms": t0 - 1000, "batches": []}
        assert eng.ingest(hello)[0] == 200

        # ship in 1-minute chunks; kproc is sent one chunk LATE (out-of-order across streams)
        offs = {s: 0 for s in streams}
        idx = {s: 0 for s in streams}
        pending_kproc = None
        for minute in range(17):
            hi_ms = t0 + (minute + 1) * 60_000
            body = {"session": "selftest", "pid": 999, "clock_ms": hi_ms, "batches": []}
            for s, lines in streams.items():
                n = 0
                while idx[s] + n < len(lines) and json.loads(lines[idx[s] + n])["TimeStamp"] < iso(hi_ms):
                    n += 1
                b, offs[s] = batches(s, lines, idx[s], n, offs[s])
                idx[s] += n
                if s == "kproc":
                    b, pending_kproc = pending_kproc, b
                if b and b["lines"]:
                    body["batches"].append(b)
            code, resp = eng.ingest(body)
            assert code == 200, resp
            if minute == 3:                      # a retried request: everything is a duplicate
                code, resp = eng.ingest(body)
                assert code == 200 and resp["accepted"] == 0 and resp["duplicates"] > 0, resp
        eng.ingest({"session": "selftest", "pid": 999, "clock_ms": t0 + 18 * 60_000,
                    "batches": [pending_kproc]})
        # an offset gap must be rejected with the expected offset
        gap = {"stream": "kfile", "offset_start": offs["kfile"] + 50, "lines": ["{}"], "ends": [offs["kfile"] + 53]}
        code, resp = eng.ingest({"session": "selftest", "pid": 999, "batches": [gap]})
        assert code == 409 and resp["expected"]["kfile"] == offs["kfile"], resp
        # final heartbeat past the end of [base+15m, base+20m) + lateness
        eng.ingest({"session": "selftest", "pid": 999, "clock_ms": base + 21 * 60_000, "batches": []})
        eng.drain()

        wins = eng.windows()
        st = eng.status()["session"]
        print(json.dumps([{k: w[k] for k in ("window", "status", "edges", "nodes", "flagged")} for w in wins], indent=1))
        print(json.dumps({k: st[k] for k in ("late_dropped", "excluded", "edges", "unresolved")}, indent=1))
        assert [w["status"] for w in wins] == ["partial", "scored", "scored", "scored"], wins
        assert [w["start_ms"] - base for w in wins] == [0, 300_000, 600_000, 900_000]
        assert st["late_dropped"] > 0, "the late kproc batch should have produced late events"
        assert sum(st["excluded"].values()) > 0 and all(k.startswith("shipper_") for k in st["excluded"])
        sdir = os.path.join(d, "sessions", "selftest")
        w1 = wins[1]["window"]
        for f in ("edges.csv", "node_attrs.csv", "features_model.csv", "scores.csv", "subgraphs.json"):
            assert os.path.exists(os.path.join(sdir, "windows", w1, f)), f
        we = pd.read_csv(os.path.join(sdir, "windows", w1, "edges.csv"))
        assert we["timestamp"].between(wins[1]["start_ms"], wins[1]["end_ms"] - 1).all()
        assert not we["source-id"].str.startswith("p999_").any(), "shipper edges leaked"
        attrs = pd.read_csv(os.path.join(sdir, "windows", w1, "node_attrs.csv"))
        assert attrs["node_attr"].str.startswith("c:\\windows\\").any(), "dos_map not applied to image loads"
        eng.flush_raw()
        raw_lines = sum(1 for _ in open(os.path.join(sdir, "raw", "kfile.json")))
        assert raw_lines == len(streams["kfile"]), (raw_lines, len(streams["kfile"]))
        assert any(x["type"] == "window" for x in seen)
        assert eng.window_detail(w1)["top"], "no top nodes"
    print("\n[SELFTEST OK] dedup, 409 on gap, late events counted, shipper PID excluded, "
          "partial window skipped, 3 windows scored, artefacts written")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
