#!/usr/bin/env python3
"""
correlate.py - link what each host's detector found into one cross-host picture. [OURS], DEVIATIONS D13.

[PAPER] §7 leaves lateral movement out of scope: OCR-APT does not process network traffic logs. Each host's model only
sees that host's 5-min graph, so no model can see a connection as two ends of one thing. This stage can. Plain code finds
the link; the LLM only explains it (the LLM runs on red windows and within a budget, so it could not find a link to a
host that stayed green). Nothing here changes a host's own window: every result is an overlay.

    every host's raw knet line --add_line()--> flow table, one bucket per window start
      keep only flows whose peer is another monitored host, drop broadcast / multicast
    every closed window ---------on_window()-> window table {host: {start_ms: alert, score, folder}}
      -> evaluate(): for each flow not in the baseline
           link       a unicast flow between monitored hosts, new to the baseline
           suspected  ... and one end has a red window at or before the flow
           confirmed  ... and the other end also has a red window in range
      -> sessions/campaigns.jsonl (last line of an id wins) + listeners
      -> suspected / confirmed: LLM thread: both hosts' subgraphs + the link in one document (alg2.investigate_campaign)

Why raw lines: the converter keeps only daddr:dport, so on the receiving host the NET node is the peer's ephemeral port,
and the two ends cannot be matched from edges. Reading raw lines here also leaves the converter's sha256 unchanged.

Address fields (verified in Loki, 2026-11-07): TCP has saddr = this host in both directions; UDP receive has daddr = this
host, or a broadcast address. So parse_flow() compares both fields with the host's own IP instead of trusting the
direction, which covers all of it and drops broadcast without a special case.

    uv run python src/live/correlate.py --selftest
"""
from __future__ import annotations

import csv
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SRC, "pipeline"))

HOSTS_FILE = "/etc/nt2102-hosts.json"          # {"ws-etw-0": "<ip>", ...}, written by Ansible from the inventory
BASELINE_FILE = os.path.join(SRC, "artifacts", "flow-baseline.json")
LEVELS = ("link", "suspected", "confirmed")
DYNAMIC_PORT = 49152                           # both ends above this: an RPC-style dynamic port, grouped as "dyn"
MAX_DOC_LINES = 400                            # same bound as alg2.serialize
MAX_EVIDENCE_WINDOWS = 3                       # per host


def load_hosts(path: str = HOSTS_FILE) -> dict:
    """{host: ip}, or {} when the file is missing: correlation is then off."""
    try:
        with open(path, encoding="utf-8") as fh:
            return {str(k): str(v) for k, v in json.load(fh).items()}
    except (OSError, ValueError):
        return {}


def load_baseline(path: str = BASELINE_FILE) -> set | None:
    """Known flow keys (`lab flows`), or None when there is no baseline yet."""
    try:
        with open(path, encoding="utf-8") as fh:
            return set(json.load(fh)["flows"])
    except (OSError, ValueError, KeyError):
        return None


def _lower(d) -> dict:
    return {str(k).lower(): v for k, v in d.items()} if isinstance(d, dict) else {}


def parse_flow(ev: dict, host: str, ips: dict) -> dict | None:
    """One raw knet event seen on `host` -> a flow between two monitored hosts, else None.

    ev: the parsed JSON line; ips: {host: ip}. The side with the lower port is taken as the server (the side listening on
    a well-known port), so both hosts' events of one connection give the same key."""
    e = _lower(ev)
    name = str(e.get("eventname") or "").lower()
    proto = "tcp" if "tcpip" in name else "udp" if "udpip" in name else None
    sent, recv = ("sent" in name or "send" in name), ("receiv" in name or "recv" in name)
    if proto is None or sent == recv:
        return None
    d = _lower(e.get("xmleventdata"))
    me, saddr, daddr = ips.get(host), d.get("saddr"), d.get("daddr")
    try:
        sport, dport = int(d.get("sport")), int(d.get("dport"))
    except (TypeError, ValueError):
        return None
    if saddr == me:
        peer, lport, rport = daddr, sport, dport
    elif daddr == me:
        peer, lport, rport = saddr, dport, sport
    else:
        return None                                   # broadcast / multicast: neither address is this host
    peer_host = next((h for h, ip in ips.items() if ip == peer and h != host), None)
    if peer_host is None:
        return None
    if lport < rport:
        client, server, port = peer_host, host, lport
    elif rport < lport:
        client, server, port = host, peer_host, rport
    else:                                             # e.g. 138 <-> 138: no server side, order the names
        client, server = sorted((host, peer_host))
        port = lport
    label = str(port)
    if port >= DYNAMIC_PORT:                          # no well-known side: direction would flip with the ports
        client, server = sorted((host, peer_host))
        label = "dyn"
    try:
        pid = int(str(d.get("_pid") or d.get("pid")).replace(",", ""))
    except ValueError:
        pid = None
    return {"key": f"{client}>{server}/{proto}/{label}", "client": client, "server": server, "proto": proto,
            "port": label, "observer": host, "pid": pid, "sent": sent}


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Correlator:
    def __init__(self, ips: dict, sessions_dir: str, window_ms: int = 300_000, baseline: set | None = None,
                 lookback_windows: int = 3, llm=None, take_budget=None, log=print):
        self.ips, self.dir, self.w_ms, self.baseline = ips, sessions_dir, int(window_ms), baseline
        self.span_ms = int(lookback_windows) * self.w_ms   # how far apart two ends of a story may be
        self.llm, self.take_budget, self.log = llm, take_budget, log
        self.path = os.path.join(sessions_dir, "campaigns.jsonl")
        self.lock = threading.Lock()
        self.flows: dict[int, dict] = {}              # window start -> {key: flow record}
        self.wins: dict[str, dict] = {h: {} for h in ips}   # host -> {window start: state}
        self.campaigns: dict[str, dict] = {}          # id -> campaign
        self.open: dict[str, str] = {}                # flow key -> id of its running campaign
        self.listeners = []
        self.llm_jobs = queue.Queue()
        os.makedirs(sessions_dir, exist_ok=True)
        self._load()
        self.thread = threading.Thread(target=self._llm_loop, daemon=True, name="correlate-llm")
        self.thread.start()

    # ---------------------------------------------------------------- persistence
    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    c = json.loads(line)
                    self.campaigns[c["id"]] = c
                except (ValueError, KeyError):
                    pass                              # a line torn by a crash
        for c in sorted(self.campaigns.values(), key=lambda x: x["last_ms"]):
            self.open[c["key"]] = c["id"]             # a restart re-reads up to 1 h: extend, do not repeat

    def _save(self, c: dict):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(c) + "\n")

    def _emit(self, c: dict):
        for cb in list(self.listeners):
            try:
                cb({"type": "campaign", "campaign": c})
            except Exception:
                pass

    # ---------------------------------------------------------------- input
    def add_line(self, host: str, ev: dict, t_ms: int):
        f = parse_flow(ev, host, self.ips)
        if f is None:
            return
        start = t_ms - t_ms % self.w_ms
        with self.lock:
            b = self.flows.setdefault(start, {})
            r = b.get(f["key"])
            if r is None:
                r = b[f["key"]] = {k: f[k] for k in ("key", "client", "server", "proto", "port")} | {
                    "count": 0, "first_ms": t_ms, "last_ms": t_ms, "seen_by": set(), "pids": {}}
            r["count"] += 1
            r["first_ms"], r["last_ms"] = min(r["first_ms"], t_ms), max(r["last_ms"], t_ms)
            r["seen_by"].add(host)
            if f["pid"] is not None:
                r["pids"].setdefault(host, set()).add(f["pid"])

    def on_window(self, host: str, rec: dict, sdir: str):
        """A window of `host` reached a final state; a scored one can lift a campaign, so evaluate then."""
        if host not in self.wins or rec.get("status") not in ("scored", "empty"):
            return
        with self.lock:
            self.wins[host][rec["start_ms"]] = {"alert": bool(rec.get("alert")), "score": rec.get("max_sg_score") or 0.0,
                                                "flagged": rec.get("flagged") or 0, "window": rec["window"], "sdir": sdir}
        self.evaluate()

    # ---------------------------------------------------------------- the rule
    def _red(self, host: str, lo: int, hi: int) -> bool:
        return any(w["alert"] and lo <= s <= hi for s, w in self.wins.get(host, {}).items())

    def _level(self, r: dict, start: int) -> tuple:
        """-> (level, origin, target). Origin = the end already red when the flow happened."""
        lo = start - self.span_ms
        client_red, server_red = self._red(r["client"], lo, start), self._red(r["server"], lo, start)
        if not (client_red or server_red):
            return "link", None, None
        origin, target = (r["client"], r["server"]) if client_red else (r["server"], r["client"])
        return ("confirmed" if self._red(target, lo, start + self.span_ms) else "suspected"), origin, target

    def _evidence(self, c: dict) -> dict:
        lo, hi = c["first_ms"] - self.span_ms - self.w_ms, c["last_ms"] + self.span_ms
        out = {}
        for h in (c["client"], c["server"]):
            ws = [{"start_ms": s, **w} for s, w in self.wins.get(h, {}).items() if lo <= s <= hi and w["flagged"]]
            ws.sort(key=lambda w: (not w["alert"], -w["score"]))
            out[h] = ws[:MAX_EVIDENCE_WINDOWS]
        return out

    def evaluate(self):
        changed, llm = [], []
        with self.lock:
            for start in sorted(self.flows):
                for key, r in self.flows[start].items():
                    if self.baseline is not None and key in self.baseline:
                        continue
                    level, origin, target = self._level(r, start)
                    cid = self.open.get(key)
                    c = self.campaigns.get(cid) if cid else None
                    if c is None or r["first_ms"] - c["last_ms"] > self.span_ms + self.w_ms:
                        cid = f"{datetime.fromtimestamp(start / 1000, timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{key}"
                        cid = cid.replace(">", "-").replace("/", "-")
                        c = self.campaigns.get(cid)
                    if c is None:
                        c = {"id": cid, "key": key, "level": "link", "client": r["client"], "server": r["server"],
                             "proto": r["proto"], "port": r["port"], "origin": None, "target": None, "first_ms": r["first_ms"],
                             "last_ms": r["last_ms"], "first_utc": _utc(r["first_ms"]), "count": 0, "buckets": {},
                             "seen_by": [], "pids": {}, "evidence": {}, "llm": None,
                             "baseline_known": self.baseline is not None}
                        self.campaigns[cid] = c
                        self.log(f"[correlate] new link {key}")
                        changed.append(c)
                    self.open[key] = cid
                    before = (c["count"], c["level"])
                    c["buckets"][str(start)] = r["count"]
                    c["count"] = sum(c["buckets"].values())
                    c["first_ms"], c["last_ms"] = min(c["first_ms"], r["first_ms"]), max(c["last_ms"], r["last_ms"])
                    c["seen_by"] = sorted(set(c["seen_by"]) | r["seen_by"])
                    for h, p in r["pids"].items():
                        c["pids"][h] = sorted(set(c["pids"].get(h, [])) | p)
                    if LEVELS.index(level) > LEVELS.index(c["level"]):     # a campaign only ever rises
                        c["level"], c["origin"], c["target"] = level, origin, target
                        self.log(f"[correlate] {key} -> {level} ({origin} -> {target})")
                        llm.append(c["id"])
                    c["evidence"] = self._evidence(c)
                    if (c["count"], c["level"]) != before and c not in changed:
                        changed.append(c)
            keep = max(self.flows, default=0) - 4 * self.span_ms - self.w_ms
            for start in [s for s in self.flows if s < keep]:
                del self.flows[start]
            for w in self.wins.values():
                for s in [s for s in w if s < keep]:
                    del w[s]
            snapshot = [json.loads(json.dumps(c)) for c in changed]
            for cid in llm:
                if self.llm is not None:
                    self.campaigns[cid]["llm"] = {"status": "pending"}
            for s in snapshot:
                s["llm"] = self.campaigns[s["id"]]["llm"]
                self._save(s)
        for s in snapshot:
            self._emit(s)
        if self.llm is not None:
            for cid in llm:
                self.llm_jobs.put(cid)

    # ---------------------------------------------------------------- LLM
    def _llm_loop(self):
        import alg2
        while True:
            cid = self.llm_jobs.get()
            try:
                with self.lock:
                    c = json.loads(json.dumps(self.campaigns[cid]))
                if self.take_budget and not self.take_budget():
                    state = {"status": "skipped", "error": "daily LLM budget reached; the campaign stands"}
                else:
                    doc = build_document(c, self.dir)
                    window = {"window": cid, "start_utc": _utc(c["first_ms"]), "end_utc": _utc(c["last_ms"])}
                    score = sum(w["score"] for ws in c["evidence"].values() for w in ws)
                    flagged = sum(w["flagged"] for ws in c["evidence"].values() for w in ws)
                    res = alg2.investigate_campaign(doc, window, flagged, round(score, 2), self.llm,
                                                    os.path.join(self.dir, "campaigns", cid))
                    state = {"status": res["verdict"], **res}
            except Exception as e:                    # investigate_campaign catches provider errors; this is a bug guard
                import traceback
                traceback.print_exc()
                state = {"status": "error", "error": f"{type(e).__name__}: {e}"}
            finally:
                self.llm_jobs.task_done()
            with self.lock:
                c = self.campaigns[cid]
                c["llm"] = state
                self._save(c)
                out = json.loads(json.dumps(c))
            self._emit(out)

    # ---------------------------------------------------------------- read side
    def list(self, limit: int = 100) -> list:
        with self.lock:
            rows = sorted(self.campaigns.values(), key=lambda c: c["last_ms"], reverse=True)[:limit]
            return json.loads(json.dumps(rows))

    def get(self, cid: str) -> dict | None:
        with self.lock:
            c = self.campaigns.get(cid)
            return json.loads(json.dumps(c)) if c else None

    def status(self) -> dict:
        with self.lock:
            n = {lv: sum(c["level"] == lv for c in self.campaigns.values()) for lv in LEVELS}
        return {"hosts": self.ips, "baseline": None if self.baseline is None else len(self.baseline), "campaigns": n}

    def drain(self):
        self.llm_jobs.join()


# --------------------------------------------------------------------------- the LLM document
def _process_names(wdir: str, pids: list) -> list:
    """PIDs -> image names, from this window's node table (process nodes are "p<pid>_...")."""
    want = {f"p{p}_" for p in pids}
    out = set()
    try:
        with open(os.path.join(wdir, "node_attrs.csv"), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["node_type"] == "PROCESS" and any(r["node"].startswith(w) for w in want):
                    out.add(r["node_attr"].replace("/", "\\").split("\\")[-1])
    except OSError:
        pass
    return sorted(out)


def build_document(c: dict, sessions_dir: str, max_lines: int = MAX_DOC_LINES) -> dict:
    """Both hosts' subgraphs + the link, one document. Link lines first, so a cut never drops them.

    A host with no kept subgraph contributes its best one anyway: that is often the point of correlating, since a short
    hop can stay under SG-Det's cut-off on the host it reached."""
    import pandas as pd
    import alg2
    import train_gnn_models as tgm
    lines, names = [], {}
    for host, ws in c["evidence"].items():
        for w in ws:
            wdir = os.path.join(sessions_dir, w["sdir"], "windows", w["window"]) if not os.path.isabs(w["sdir"]) \
                else os.path.join(w["sdir"], "windows", w["window"])
            names.setdefault(host, set()).update(_process_names(wdir, c["pids"].get(host, [])))
            try:
                sgs = json.load(open(os.path.join(wdir, "subgraphs.json")))["subgraphs"]
                edges = tgm.normalise_edges(pd.read_csv(os.path.join(wdir, "edges.csv"),
                                                        dtype={"source-id": str, "destination-id": str}))
            except (OSError, ValueError, KeyError):
                continue
            for sg in ([g for g in sgs if g.get("kept")] or sgs[:1]):
                lines += [f"[{host}] {ln}" for ln in alg2.serialize(sg, edges, max_lines)["lines"]]
    when = alg2.display_time(_utc(c["first_ms"]))
    net = []
    for h in (c["client"], c["server"]):
        who = ", ".join(sorted(names.get(h, []))) or "an unresolved process"
        net.append(f"[network] {who} on {h} took part in {c['proto']}/{c['port']} between {c['client']} (client) and "
                   f"{c['server']} (server), {c['count']} network events, first seen {when}.")
    keep = list(dict.fromkeys(net + lines))
    cut = max(0, len(keep) - max_lines)
    keep = keep[:max_lines]
    return {"text": "".join(f"{ln} \n" for ln in keep), "lines": keep, "truncated": cut, "n_events": c["count"]}


# --------------------------------------------------------------------------- selftest
def selftest():
    """'It runs' + invariants on synthetic flows. Never evidence that real cross-host movement is detected."""
    import tempfile
    import alg2
    ips = {"h0": "10.0.0.10", "h1": "10.0.0.11"}
    tcp_s, tcp_r = "KERNEL_NETWORK_TASK_TCPIP/Datasent.", "KERNEL_NETWORK_TASK_TCPIP/Datareceived."
    udp_s, udp_r = "KERNEL_NETWORK_TASK_UDPIP/DatasentoverUDPprotocol.", "KERNEL_NETWORK_TASK_UDPIP/DatareceivedoverUDPprotocol."

    def ev(name, saddr, daddr, sport, dport, pid=700):
        return {"EventName": name, "ProcessID": 0, "ProcessName": "Idle",
                "XmlEventData": {"PID": str(pid), "saddr": saddr, "daddr": daddr, "sport": str(sport), "dport": str(dport)}}

    # parsing: real field shapes from Loki. TCP keeps saddr = this host both ways; UDP receive flips.
    a = parse_flow(ev(tcp_s, "10.0.0.10", "10.0.0.11", 51000, 445), "h0", ips)
    b = parse_flow(ev(tcp_r, "10.0.0.11", "10.0.0.10", 445, 51000), "h1", ips)
    assert a["key"] == b["key"] == "h0>h1/tcp/445", (a, b)
    u = parse_flow(ev(udp_r, "10.0.0.10", "10.0.0.11", 51000, 53), "h1", ips)
    assert u["key"] == "h0>h1/udp/53", u
    assert parse_flow(ev(udp_r, "10.0.0.10", "10.0.0.255", 138, 138), "h1", ips) is None, "broadcast kept"
    assert parse_flow(ev(udp_r, "10.0.0.10", "224.0.0.251", 5353, 5353), "h1", ips) is None, "multicast kept"
    assert parse_flow(ev(tcp_s, "10.0.0.10", "8.8.8.8", 51000, 443), "h0", ips) is None, "unmonitored peer kept"
    assert parse_flow(ev("KERNEL_NETWORK_TASK_TCPIP/connectionattempted", "10.0.0.10", "10.0.0.11", 1, 2), "h0", ips) is None
    assert parse_flow(ev(tcp_s, "10.0.0.10", "10.0.0.11", 50000, 50001), "h0", ips)["key"] == "h0>h1/tcp/dyn"
    assert parse_flow(ev(tcp_s, "10.0.0.10", "10.0.0.11", 50001, 50000), "h0", ips)["key"] == "h0>h1/tcp/dyn"
    assert parse_flow(ev(udp_s, "10.0.0.10", "10.0.0.11", 138, 138), "h0", ips)["key"] == "h0>h1/udp/138"
    assert "several Windows hosts" in alg2.CAMPAIGN_TRIAGE_SYSTEM and "[network]" in alg2.CAMPAIGN_TRIAGE_SYSTEM
    assert "one Windows 10 host" in alg2.TRIAGE_SYSTEM, "the per-host prompt must stay as it was"

    base = 1_791_000_000_000                         # multiple of 5 min
    w = 300_000

    def rec(k, alert=False, flagged=0, score=0.0):
        return {"window": f"w{k}", "start_ms": base + k * w, "status": "scored", "alert": alert, "flagged": flagged,
                "max_sg_score": score}

    with tempfile.TemporaryDirectory() as d:
        # one fake window on disk per host, for the LLM document
        for host, tool, wid in (("h0", "tool0.exe", "w1"), ("h1", "tool1.exe", "w2")):
            wd = os.path.join(d, f"s-{host}", "windows", wid)
            os.makedirs(wd)
            with open(os.path.join(wd, "edges.csv"), "w") as fh:
                fh.write("source-id,source-type,destination-id,destination-type,edge-type,timestamp\n"
                         f"p700_1,PROCESS,f1,FILE,Write,{base + w + 1000}\n")
            with open(os.path.join(wd, "node_attrs.csv"), "w") as fh:
                fh.write(f"node,node_type,node_attr\np700_1,PROCESS,c:\\x\\{tool}\nf1,FILE,c:\\x\\out.bin\n")
            json.dump({"subgraphs": [{"id": 0, "seed": "p700_1", "kept": host == "h0", "sg_score": 12.0, "level": "Moderate",
                                      "n_anomalous": 2, "n_connectors": 0,
                                      "nodes": [{"id": "p700_1", "type": "process", "attr": f"c:\\x\\{tool}", "role": "anomalous"},
                                                {"id": "f1", "type": "file", "attr": "c:\\x\\out.bin", "role": "anomalous"}]}]},
                      open(os.path.join(wd, "subgraphs.json"), "w"))
        seen = []
        c = Correlator(ips, d, w, baseline={"h0>h1/tcp/135"}, llm=alg2.MockLLM(alg2.rule_mock_responder),
                       take_budget=lambda: True, log=lambda *_: None)
        c.listeners.append(seen.append)

        # a known flow stays silent; broadcast never counts
        c.add_line("h0", ev(tcp_s, "10.0.0.10", "10.0.0.11", 51000, 135), base + 1000)
        c.add_line("h1", ev(udp_r, "10.0.0.10", "10.0.0.255", 138, 138), base + 1000)
        c.on_window("h0", rec(0), "s-h0")
        assert c.list() == [], c.list()

        # link: a new flow, nobody red, no LLM call
        c.add_line("h0", ev(tcp_s, "10.0.0.10", "10.0.0.11", 51001, 445), base + w + 2000)
        c.add_line("h1", ev(tcp_r, "10.0.0.11", "10.0.0.10", 445, 51001), base + w + 2100)
        c.on_window("h1", rec(1), "s-h1")
        got = c.list()
        assert len(got) == 1 and got[0]["level"] == "link" and got[0]["llm"] is None, got
        assert got[0]["seen_by"] == ["h0", "h1"] and got[0]["count"] == 2, got[0]

        # suspected: the client host goes red in that same window
        c.on_window("h0", rec(1, True, 12, 15.0), "s-h0")
        got = c.get(c.list()[0]["id"])
        assert got["level"] == "suspected" and (got["origin"], got["target"]) == ("h0", "h1"), got
        c.drain()

        # confirmed: the other host goes red one window later (it can arrive after the flow)
        c.on_window("h1", rec(2, True, 11, 11.0), "s-h1")
        c.drain()
        got = c.get(c.list()[0]["id"])
        assert got["level"] == "confirmed" and got["llm"]["status"] in ("attack", "false_positive", "uncertain"), got

        # never falls back; the same flow later extends the campaign instead of opening another
        c.add_line("h0", ev(tcp_s, "10.0.0.10", "10.0.0.11", 51002, 445), base + 2 * w + 500)
        c.on_window("h0", rec(2), "s-h0")
        assert len(c.list()) == 1 and c.list()[0]["level"] == "confirmed" and c.list()[0]["count"] == 3, c.list()

        # the LLM document holds both hosts, tagged, and the link first; a host below the cut-off still contributes
        doc = build_document(c.list()[0], d)
        assert doc["lines"][0].startswith("[network] ") and "tool0.exe" in doc["lines"][0], doc["lines"][:2]
        assert any(x.startswith("[h0] ") for x in doc["lines"]) and any(x.startswith("[h1] ") for x in doc["lines"]), doc
        assert os.path.exists(os.path.join(d, "campaigns", c.list()[0]["id"], "doc.txt"))
        assert any(e["type"] == "campaign" for e in seen)

        # a restart reloads the campaign; the same flow does not open a second one
        c2 = Correlator(ips, d, w, baseline={"h0>h1/tcp/135"}, log=lambda *_: None)
        assert len(c2.list()) == 1 and c2.list()[0]["level"] == "confirmed"
        c2.add_line("h0", ev(tcp_s, "10.0.0.10", "10.0.0.11", 51003, 445), base + 2 * w + 900)
        c2.on_window("h0", rec(2), "s-h0")
        assert len(c2.list()) == 1, c2.list()

        # budget spent: the campaign stands, the LLM is skipped
        d2 = os.path.join(d, "b")
        c3 = Correlator(ips, d2, w, baseline=set(), llm=alg2.MockLLM(alg2.rule_mock_responder),
                        take_budget=lambda: False, log=lambda *_: None)
        c3.add_line("h1", ev(tcp_s, "10.0.0.11", "10.0.0.10", 51000, 3389), base + 1000)
        c3.on_window("h1", rec(0, True, 11, 11.0), "s-h1")
        c3.drain()
        got = c3.list()[0]
        assert got["level"] == "suspected" and got["llm"]["status"] == "skipped", got
        assert (got["origin"], got["target"]) == ("h1", "h0"), got

        # no baseline yet: flows are still reported, and the record says the baseline was missing
        c4 = Correlator(ips, os.path.join(d, "n"), w, baseline=None, log=lambda *_: None)
        c4.add_line("h0", ev(tcp_s, "10.0.0.10", "10.0.0.11", 51000, 135), base + 1000)
        c4.on_window("h0", rec(0), "s-h0")
        assert c4.list()[0]["baseline_known"] is False and c4.status()["baseline"] is None
    assert load_hosts("/nonexistent") == {} and load_baseline("/nonexistent") is None
    two_engines()
    print("[SELFTEST OK] TCP/UDP address parsing, broadcast + multicast + unmonitored peers dropped, baseline silences known "
          "flows, link -> suspected -> confirmed, levels never fall, later flows extend a campaign, LLM document holds both "
          "hosts, restart reloads, budget respected, two engines with different models, one-host results unchanged")


def two_engines():
    """Two real Engines with different tiny models feed one Correlator, and a host's windows match a plain Engine's."""
    import tempfile
    import alg2
    import encode_to_features as enc
    from engine import Engine
    import score_window as sw
    kp, kf, kn = ("Microsoft-Windows-Kernel-" + x for x in ("Process", "File", "Network"))
    base = 1_791_000_000_000
    t0 = base + 60_000
    ips = {"h0": "10.0.0.10", "h1": "10.0.0.11"}

    def iso(ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "0+00:00"

    def line(prov, name, pid, pname, ms, **data):
        return json.dumps({"ProviderName": prov, "EventName": name, "ProcessID": pid, "ProcessName": pname,
                           "TimeStamp": iso(ms), "XmlEventData": data})

    def capture(me, peer, port_first):
        s = {"kproc": [line(kp, "ProcessStart/Start", 100, "explorer", t0, ProcessID="200", ParentProcessID="100",
                            ImageName=r"\Device\HarddiskVolume4\Windows\System32\cmd.exe")], "kfile": [], "knet": []}
        for i in range(14 * 60 // 5):
            t = t0 + 1000 + i * 5000
            s["kproc"].append(line(kp, "ImageLoad", 200, "cmd", t, ProcessID="200",
                                   ImageName=rf"\Device\HarddiskVolume4\Windows\System32\lib{i % 7}.dll"))
            s["kfile"].append(line(kf, "Create", 200, "cmd", t + 1, FileObject=f"0x{i:x}", FileName=rf"C:\tmp\f{i % 9}.txt"))
            s["kfile"].append(line(kf, "Write", 200, "cmd", t + 2, FileObject=f"0x{i:x}"))
            sa, da, sp, dp = (me, peer, 51000, 445) if port_first else (me, peer, 445, 51000)
            s["knet"].append(line(kn, "KERNEL_NETWORK_TASK_TCPIP/Datasent.", 4, "System", t + 3, PID="200",
                                  saddr=sa, daddr=da, sport=str(sp), dport=str(dp)))
        return s

    def feed(eng, sess, streams):
        off = {k: 0 for k in streams}
        for hi in range(1, 17):
            hi_ms = t0 + hi * 60_000
            body = {"session": sess, "mode": "replay", "exclude_pids": [], "host": eng.host or "h0", "clock_ms": hi_ms,
                    "dos_map": {r"\Device\HarddiskVolume4": "c:"}, "batches": []}
            for k, lines in streams.items():
                got = [x for x in lines if iso(hi_ms - 60_000) <= json.loads(x)["TimeStamp"] < iso(hi_ms)]
                ends, pos = [], off[k]
                for x in got:
                    pos += len(x) + 1
                    ends.append(pos)
                if got:
                    body["batches"].append({"stream": k, "offset_start": off[k], "lines": got, "ends": ends})
                    off[k] = pos
            assert eng.ingest(body)[0] == 200
        eng.ingest({"session": sess, "exclude_pids": [], "clock_ms": base + 21 * 60_000, "batches": []})
        eng.drain()

    with tempfile.TemporaryDirectory() as d:
        runs = {}
        for name, seed_edges in (("m0", 8), ("m1", 9)):
            e = sw.synthetic_edges()
            os.makedirs(f"{d}/{name}")
            e.to_csv(f"{d}/{name}/edges.csv", index=False)
            _, feats, sc = enc.build_features(enc.normalise_edges(e))
            feats.to_csv(f"{d}/{name}/features_model.csv", index_label="node_uuid")
            json.dump({"feature_order": enc.ACTION_COLS + enc.IDLE_COLS, "idle_minmax": sc, "ts_unit": "ms",
                       "repo_rounding": True, "refit": True}, open(f"{d}/{name}/scaler.json", "w"))
            sw.tgm.run(sw.tgm.parse_args(["--edges", f"{d}/{name}/edges.csv", "--features", f"{d}/{name}/features_model.csv",
                                          "--scaler", f"{d}/{name}/scaler.json", "--out-dir", f"{d}/{name}/run",
                                          "--epochs", str(seed_edges), "--runs", "1", "--purge-windows", "1"]))
            runs[name] = (f"{d}/{name}/run", f"{d}/{name}/scaler.json")

        sess = f"{d}/sessions"
        cor = Correlator(ips, sess, 300_000, baseline=set(), log=lambda *_: None)
        from history import HistoryStore
        shared = HistoryStore(sess)
        engines = {}
        for h, m in (("h0", "m0"), ("h1", "m1")):
            e = Engine(*runs[m], sess, window_min=5, lateness_s=20, host=h, history=shared, flow_sink=cor.add_line)

            def fwd(ev, h=h, e=e):
                if ev["type"] == "window":
                    cor.on_window(h, ev["window"], os.path.basename(e.session["dir"]))
            e.listeners.append(fwd)
            engines[h] = e
        feed(engines["h0"], "run-a", capture("10.0.0.10", "10.0.0.11", True))
        feed(engines["h1"], "run-a", capture("10.0.0.11", "10.0.0.10", False))
        assert engines["h0"].session["dir"] != engines["h1"].session["dir"], "same run name must not share a folder"
        assert engines["h0"].run_dir != engines["h1"].run_dir, "each host loads its own model"
        q = shared.query(base, base + 20 * 60_000, include_replay=True)["windows"]
        assert {w["host"] for w in q} == {"h0", "h1"}, q
        assert any(c["key"] == "h0>h1/tcp/445" and c["seen_by"] == ["h0", "h1"] for c in cor.list()), cor.list()

        # same lines through a plain Engine: identical windows, so two-host mode leaves a host's result alone
        plain = Engine(*runs["m0"], f"{d}/plain", window_min=5, lateness_s=20)
        feed(plain, "run-a", capture("10.0.0.10", "10.0.0.11", True))
        keys = ("window", "status", "edges", "nodes", "flagged", "subgraphs", "max_sg_score", "alert")
        a = [{k: w.get(k) for k in keys} for w in engines["h0"].windows()]
        b = [{k: w.get(k) for k in keys} for w in plain.windows()]
        assert a == b and any(w["status"] == "scored" for w in a), (a, b)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
