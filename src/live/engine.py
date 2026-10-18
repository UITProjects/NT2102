#!/usr/bin/env python3
"""
engine.py - live windowed OCR-APT scoring, no web code (live_server.py is the thin HTTP layer).

    shipper batches (raw SilkETW JSON lines + byte offsets + VM clock)
      -> ingest(): idempotent by offset, raw lines appended to sessions/<id>/raw/<stream>.json
      -> reorder heap keyed (ts, stream, offset)        same tie order as etw2nodlink's heapq.merge
      -> released when ts < watermark = clock_ms - lateness
      -> LiveGraph = etw2nodlink.Graph (the SAME converter code that built the training edges,
                     loaded from src/pipeline/etw2nodlink.py) + the shipper's own PID excluded
      -> edge buffer (sorted by ts)
      -> window [start, start + w) closes when watermark >= end
      -> scoring thread: score_window.score_edges (train scaler + train relations)
                         -> alg1.construct (Alg. 1 + SG-Det; window alert = >= 1 subgraph kept)
      -> llm thread (only alerted windows, only with --llm-provider): alg2.investigate
                         -> verdict attack / false_positive / uncertain + attack report (Alg. 2 l.3-15)
      -> sessions/<id>/windows/<wid>/*.csv|json  + listeners (SSE in live_server.py)

Time: only the shipper's clock is used (clock_ms), never this host's wall clock, so a replay at
any speed gives the same windows as the live capture would. Deviations: docs/DEVIATIONS.md D8.

Usage (from the repo root)
    uv run python src/live/engine.py --selftest
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
import alg1  # noqa: E402
import alg2  # noqa: E402
import score_window as sw  # noqa: E402

EDGE_HEADER = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
DEFAULT_CONVERTER = os.path.join(SRC, "pipeline", "etw2nodlink.py")
MAX_FLAGGED_FOR_SUBGRAPHS = 300   # a broken model flags everything; keep the UI usable


# --------------------------------------------------------------------------- converter reuse
def load_converter(converter_path: str = DEFAULT_CONVERTER):
    """Import etw2nodlink.py (src/pipeline) and return (module, sha256) for the converter-check.

    The sha is sha256 of the raw file bytes, no normalisation. Ansible copies this file to the VM
    unchanged, so it equals Get-FileHash there, which is how 07-build-train-dataset.ps1 records
    experiment.json tools.converter. .gitattributes pins the file to LF so every clone has the same
    bytes. Edit the converter before a training capture, never after: any byte change gives a new sha."""
    raw = open(converter_path, "rb").read()
    mod = types.ModuleType("etw2nodlink")
    mod.__file__ = converter_path
    exec(compile(raw.decode("utf-8"), mod.__file__, "exec"), mod.__dict__)
    return mod, hashlib.sha256(raw).hexdigest()


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


def _f(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def utc_id(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- engine
class Engine:
    def __init__(self, run_dir, scaler_path, sessions_dir, window_min=5, stride_min=None, lateness_s=20,
                 origin_ms=0, converter_path=DEFAULT_CONVERTER, experiment_json=None, allow_collapsed=False, top_k=20,
                 n_seed=15, min_nodes=3, max_edges=5000, sg_min_level="Moderate", sg_min_score=None,
                 sg_moderate_from=alg1.MODERATE_FROM, llm_provider=None, llm_model=None, llm_base_url=None,
                 llm_client=None, llm_max_subgraphs=5):
        self.conv, self.conv_sha = load_converter(converter_path)
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
        # Alg. 1 parameters: [PAPER] n_seed 15, max_e 5000, keep >= Moderate; [AUTHOR CODE] min_nodes 3;
        # [OURS] Moderate from sg_score 30 (DEVIATIONS D8)
        self.sg = {"n_seed": n_seed, "min_nodes": min_nodes, "max_edges": max_edges,
                   "min_level": sg_min_level, "min_score": sg_min_score, "moderate_from": sg_moderate_from}

        # LLM triage + report after SG-Det (alg2.py); None = off. Own thread: one alert can take tens of
        # seconds of LLM calls and must not delay scoring of the next window.
        self.llm = llm_client or (alg2.make_client(llm_provider, llm_model, llm_base_url) if llm_provider else None)
        self.llm_cfg = {"provider": llm_provider or ("custom" if llm_client else None),
                        "model": getattr(self.llm, "model", None), "max_subgraphs": llm_max_subgraphs}

        self.lock = threading.RLock()
        self.listeners = []                       # callables(event_dict), called from any thread
        self.jobs = queue.Queue()
        self.llm_jobs = queue.Queue()
        self.session = None
        threading.Thread(target=self._scoring_loop, daemon=True, name="scoring").start()
        threading.Thread(target=self._llm_loop, daemon=True, name="llm").start()

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
                "tau": self.run.tau, "score_range": self.run.score_range, "alg1": self.sg, "llm": self.llm_cfg,
                "created_utc": utc_iso(int(time.time() * 1000))}
        json.dump(meta, open(os.path.join(sdir, "session.json"), "w"), indent=2)
        with open(os.path.join(sdir, "windows.csv"), "w", newline="") as fh:
            csv.writer(fh).writerow(["window", "start_ms", "end_ms", "start_utc", "status", "edges", "nodes",
                                     "flagged", "subgraphs", "subgraphs_kept", "max_sg_score", "max_level",
                                     "alert", "edges_dropped_relation", "llm"])
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
                   "nodes": 0, "flagged": 0, "flagged_by_type": {}, "subgraphs": 0, "subgraphs_kept": 0,
                   "max_sg_score": 0.0, "max_level": "", "alert": False}
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
        # a broken model flags everything; cap the anomalous set so Alg. 1 and the UI stay usable
        cand = nodes
        truncated = int(nodes["flagged"].sum()) > MAX_FLAGGED_FOR_SUBGRAPHS
        if truncated:
            keep = set(nodes[nodes["flagged"]].nlargest(MAX_FLAGGED_FOR_SUBGRAPHS, "margin")["node_uuid"])
            cand = nodes.assign(flagged=nodes["flagged"] & nodes["node_uuid"].isin(keep))
        edges_norm = sw.tgm.normalise_edges(edges)
        subgraphs, sg_stats = alg1.construct(cand, edges_norm, attrs, self.run.score_range,
                                             self.sg["n_seed"], self.sg["min_nodes"], self.sg["max_edges"],
                                             self.sg["min_level"], self.sg["min_score"],
                                             self.sg["moderate_from"])
        sg_sum = alg1.summarise(subgraphs)
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
            json.dump({"window": wid, "truncated": truncated, "alg1": self.sg, "stats": sg_stats,
                       "subgraphs": subgraphs},
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
                       **sg_sum, alert=sg_sum["subgraphs_kept"] > 0, scoring_ms=ms,
                       edges_dropped_relation=info["edges_dropped_relation"],
                       max_margin=_f(nodes["margin"].max()))
            S["details"][wid] = {"top": top, "subgraphs": subgraphs[:25], "truncated": truncated,
                                 "subgraphs_total": len(subgraphs), "alg1_stats": sg_stats}
            if rec["alert"] and self.llm:
                self._queue_llm(S, rec, wdir, subgraphs, edges_norm)
            self._write_index(S, rec, info)
            self._emit({"type": "window", "window": rec})

    # ---------------------------------------------------------------- LLM triage + report (alg2.py)
    def _queue_llm(self, S, rec, wdir, subgraphs, edges_norm):
        rec["llm"] = {"status": "pending"}
        win = {"window": rec["window"], "start_utc": rec["start_utc"], "end_utc": utc_iso(rec["end_ms"])}
        self.llm_jobs.put((S["id"], rec["window"], wdir, [g for g in subgraphs if g["kept"]], edges_norm, win))

    def _llm_loop(self):
        while True:
            sid, wid, wdir, kept, edges_norm, win = self.llm_jobs.get()
            try:
                try:
                    res = alg2.investigate(kept, edges_norm, win, self.llm, os.path.join(wdir, "llm"),
                                           max_subgraphs=self.llm_cfg["max_subgraphs"])
                    summary = alg2.ui_summary(res)
                    state = {"status": res["verdict"], "confidence": res["confidence"],
                             "tactics": [t.get("id", "") for t in res["tactics"]], "ms": res["ms"]}
                except Exception as e:      # investigate() catches provider errors; this is a bug guard
                    import traceback
                    traceback.print_exc()
                    summary, state = None, {"status": "error", "error": f"{type(e).__name__}: {e}"}
                with self.lock:
                    S = self.session
                    if S is None or S["id"] != sid:
                        continue
                    rec = S["by_id"][wid]
                    rec["llm"] = state
                    S["details"].setdefault(wid, {})["llm"] = summary
                    with open(os.path.join(S["dir"], "llm_verdicts.csv"), "a", newline="") as fh:
                        w = csv.writer(fh)
                        if fh.tell() == 0:
                            w.writerow(["window", "verdict", "confidence", "tactics", "ms", "model"])
                        w.writerow([wid, state["status"], state.get("confidence"), " ".join(state.get("tactics", [])),
                                    state.get("ms"), self.llm_cfg["model"]])
                    self._emit({"type": "window", "window": rec})
            finally:
                self.llm_jobs.task_done()

    def _write_index(self, S, rec, info):
        with open(os.path.join(S["dir"], "windows.csv"), "a", newline="") as fh:
            csv.writer(fh).writerow([rec["window"], rec["start_ms"], rec["end_ms"], rec["start_utc"], rec["status"],
                                     rec["edges"], rec["nodes"], rec["flagged"], rec["subgraphs"],
                                     rec["subgraphs_kept"], rec["max_sg_score"], rec["max_level"], rec["alert"],
                                     info.get("edges_dropped_relation", ""), rec.get("llm", {}).get("status", "")])

    # ---------------------------------------------------------------- read side
    def status(self) -> dict:
        with self.lock:
            base = {"model": {"run_dir": self.run_dir, "tau": self.run.tau, "scaler": self.scaler_path,
                              "converter_sha256": self.conv_sha, "converter_check": self.conv_check},
                    "params": {"window_min": self.w_ms / 60000, "stride_min": self.s_ms / 60000,
                               "lateness_s": self.lateness_ms / 1000, "origin_ms": self.origin_ms,
                               "alg1": self.sg, "llm": self.llm_cfg},
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
        """Wait until every closed window is scored and every LLM job is done (selftest / replay)."""
        self.jobs.join()
        self.llm_jobs.join()

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

        # LLM: a gated rule mock, so the test can see 'pending' before the verdict lands
        gate = {"on": False, "go": threading.Event()}

        def gated(system, messages):
            if gate["on"]:
                gate["go"].wait(10)
            return alg2.rule_mock_responder(system, messages)
        eng = Engine(f"{d}/run", f"{d}/scaler.json", f"{d}/sessions", window_min=5, lateness_s=20,
                     llm_client=alg2.MockLLM(gated))
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
        for w in wins:
            if w["status"] == "scored":
                assert "subgraphs_kept" in w and w["alert"] == (w["subgraphs_kept"] > 0), w
        idx_csv = pd.read_csv(os.path.join(sdir, "windows.csv"))
        assert {"subgraphs_kept", "max_sg_score", "max_level", "alert", "llm"} <= set(idx_csv.columns)

        # LLM path on window 1 with a hand-made kept subgraph (the tiny model rarely alerts on its own)
        en = sw.tgm.normalise_edges(we.astype({"source-id": str, "destination-id": str}))
        p = en["source-id"].iloc[0]
        dsts = list(dict.fromkeys(en.loc[en["source-id"] == p, "destination-id"]))[:2]
        types = dict(zip(en["source-id"], en["source-type"])) | dict(zip(en["destination-id"], en["destination-type"]))
        an = dict(zip(attrs["node"], attrs["node_attr"]))
        fake = {"id": 0, "seed": p, "kept": True, "sg_score": 31.0, "level": "Moderate", "n_anomalous": 3,
                "n_connectors": 0, "nodes": [{"id": n, "type": types[n], "attr": an.get(n, ""), "role": "anomalous",
                                              "score": 1.0, "tau": 0.5} for n in [p] + dsts]}
        gate["on"] = True
        with eng.lock:
            S = eng.session
            rec = S["by_id"][w1]
            eng._queue_llm(S, rec, os.path.join(sdir, "windows", w1), [fake], en)
            assert rec["llm"]["status"] == "pending", rec
        time.sleep(0.2)
        assert eng.windows()[1]["llm"]["status"] == "pending", "verdict landed before the LLM answered"
        gate["go"].set()
        eng.drain()
        got = eng.windows()[1]["llm"]
        assert got["status"] == "false_positive", got           # rule mock: no attack-tool keyword
        assert eng.window_detail(w1)["llm"]["subgraphs"][0]["evidence"], "evidence missing"
        assert os.path.exists(os.path.join(sdir, "windows", w1, "llm", "triage.json"))
        lv = pd.read_csv(os.path.join(sdir, "llm_verdicts.csv"))
        assert lv["verdict"].tolist()[-1] == "false_positive", lv
    print("\n[SELFTEST OK] dedup, 409 on gap, late events counted, shipper PID excluded, "
          "partial window skipped, 3 windows scored, Alg. 1 alert fields, artefacts written, "
          "LLM verdict pending -> final on its own thread")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
