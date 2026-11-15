#!/usr/bin/env python3
"""
correlate.py - follow an anomaly on one host to the other monitored host it talked to. [OURS], DEVIATIONS D13.

[PAPER] §7 leaves lateral movement out of scope: OCR-APT does not process network traffic logs. Each host's model only
sees that host's 5-min graph. This stage starts where the detector already said "anomalous" and asks one question:
did that anomaly talk to another monitored VM, and what happened on the other end? Nothing is reported without a red
window: the GNN score is the only gate. Every result is an overlay; no host's own window changes.

    every closed window ------on_window()-> window table {host: {start_ms: state}}
      a RED window of host A: its KEPT subgraphs (Alg. 1 + SG-Det)
        -> seeds: every NET node in them (anomalous or connector) whose IP is another monitored host P
        -> finding  A-window-P  (pending)
    every raw knet line -------add_line()-> connection index, one bucket per window start, key = the 5-tuple
      used only for the join: seed "P_ip:P_port" on A -> A's own port -> "A_ip:A_port" = the NET node on P, + both PIDs
    P's windows in [t - 1 w, t + 1 w] closed -> resolve():
        P nodes = the matched NET nodes + their owner processes + the processes' 1-hop objects, and a 2nd hop only
                  through nodes P's own model flagged; scores from P's scores.csv
        one-sided     none of them flagged, no P kept subgraph holds them
        corroborated  >= 1 of them flagged by P's model, or a P kept subgraph holds them
      -> sessions/crosshost.jsonl (last line of an id wins) + listeners
      -> LLM thread: A's kept subgraphs + P's related nodes + the join lines, one document (alg2.investigate_crosshost)

Why raw lines: the converter keeps only daddr:dport. TCP has saddr = this host in both directions (verified in Loki,
2026-11-07), so one connection A:51000 -> P:445 is the node "P_ip:445" on A and "A_ip:51000" on P. Only the 5-tuple
from the raw lines tells which of P's NET nodes (and so which P process) is the other end. Reading them here leaves the
converter's sha256 unchanged.

    uv run python src/live/correlate.py --selftest
"""
from __future__ import annotations

import csv
import json
import os
import queue
import sys
import threading
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SRC, "pipeline"))

HOSTS_FILE = "/etc/nt2102-hosts.json"          # {"ws-etw-0": "<ip>", ...}, written by Ansible from the inventory
LEVELS = ("pending", "one-sided", "corroborated")
DYNAMIC_PORT = 49152                           # both ends above this: an RPC-style dynamic port, shown as "dyn"
MAX_DOC_LINES = 400                            # same bound as alg2.serialize
MAX_PEER_NODES = 60                            # per peer window, flagged first, then by score
STALE_WINDOWS = 3                              # resolve anyway once any host is this far past the range


def load_hosts(path: str = HOSTS_FILE) -> dict:
    """{host: ip}, or {} when the file is missing: correlation is then off."""
    try:
        with open(path, encoding="utf-8") as fh:
            return {str(k): str(v) for k, v in json.load(fh).items()}
    except (OSError, ValueError):
        return {}


def _lower(d) -> dict:
    return {str(k).lower(): v for k, v in d.items()} if isinstance(d, dict) else {}


def parse_flow(ev: dict, host: str, ips: dict) -> dict | None:
    """One raw knet event seen on `host` -> one end of a connection between two monitored hosts, else None.

    ev: the parsed JSON line; ips: {host: ip}. Returns this host's port (lport), the peer's (rport) and the 5-tuple key
    `conn`, which both hosts' events of one connection share. `key` is the display name: the side with the lower port is
    taken as the server [INFERENCE: the side listening on a well-known port]."""
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
    conn = (proto,) + tuple(sorted(((host, lport), (peer_host, rport))))
    return {"key": f"{client}>{server}/{proto}/{label}", "conn": conn, "proto": proto, "host": host,
            "peer": peer_host, "lport": lport, "rport": rport, "pid": pid, "sent": sent}


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_net(attr: str) -> tuple:
    """NET node attr -> (ip, port) or (None, None). Both the SilkETW int form and dotted text are accepted."""
    import alg2
    a = alg2.decode_net_attr(str(attr or ""))
    ip, _, port = a.rpartition(":")
    try:
        return ip, int(port)
    except ValueError:
        return None, None


def _wdir(sessions_dir: str, sdir: str, window: str) -> str:
    base = sdir if os.path.isabs(sdir) else os.path.join(sessions_dir, sdir)
    return os.path.join(base, "windows", window)


def _read_csv(path: str) -> list:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _kept_subgraphs(wdir: str) -> list:
    try:
        return [g for g in json.load(open(os.path.join(wdir, "subgraphs.json")))["subgraphs"] if g.get("kept")]
    except (OSError, ValueError, KeyError):
        return []


class Correlator:
    def __init__(self, ips: dict, sessions_dir: str, window_ms: int = 300_000, llm=None, take_budget=None, log=print):
        self.ips, self.dir, self.w_ms = ips, sessions_dir, int(window_ms)
        self.llm, self.take_budget, self.log = llm, take_budget, log
        self.path = os.path.join(sessions_dir, "crosshost.jsonl")
        self.lock = threading.Lock()
        self.conns: dict[int, dict] = {}              # window start -> {5-tuple: connection record}
        self.wins: dict[str, dict] = {h: {} for h in ips}   # host -> {window start: state}
        self.findings: dict[str, dict] = {}           # id -> finding
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
                    f = json.loads(line)
                    self.findings[f["id"]] = f
                except (ValueError, KeyError):
                    pass                              # a line torn by a crash
        for f in self.findings.values():              # the window table is gone: a pending one cannot finish
            f.pop("dirty", None)
            if f["level"] == "pending":
                f["level"], f["note"] = "one-sided", "server restarted before the peer windows closed; peer side not read"

    def _save(self, f: dict):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(f) + "\n")

    def _emit(self, f: dict):
        for cb in list(self.listeners):
            try:
                cb({"type": "crosshost", "finding": f})
            except Exception:
                pass

    # ---------------------------------------------------------------- input
    def add_line(self, host: str, ev: dict, t_ms: int):
        """Raw knet line -> connection index. Only an index for the join; it never opens a finding."""
        f = parse_flow(ev, host, self.ips)
        if f is None:
            return
        start = t_ms - t_ms % self.w_ms
        with self.lock:
            b = self.conns.setdefault(start, {})
            r = b.get(f["conn"])
            if r is None:
                r = b[f["conn"]] = {"proto": f["proto"], "key": f["key"], "ports": {host: f["lport"], f["peer"]: f["rport"]},
                                    "count": 0, "first_ms": t_ms, "last_ms": t_ms, "seen_by": set(), "pids": {}}
            r["count"] += 1
            r["first_ms"], r["last_ms"] = min(r["first_ms"], t_ms), max(r["last_ms"], t_ms)
            r["seen_by"].add(host)
            if f["pid"] is not None:
                r["pids"].setdefault(host, set()).add(f["pid"])

    def on_window(self, host: str, rec: dict, sdir: str):
        """A window of `host` reached a final state: a red one may open a finding, any one may finish a pending one."""
        if host not in self.wins or rec.get("status") not in ("scored", "empty"):
            return
        start = rec["start_ms"]
        with self.lock:
            self.wins[host][start] = {"alert": bool(rec.get("alert")), "score": rec.get("max_sg_score") or 0.0,
                                      "flagged": rec.get("flagged") or 0, "window": rec["window"], "sdir": sdir}
            if rec.get("alert"):
                self._open(host, start, self.wins[host][start])
        self.evaluate()

    # ---------------------------------------------------------------- the rule
    def _seeds(self, host: str, w: dict) -> dict:
        """NET nodes of the window's kept subgraphs that point at another monitored host -> {peer: [seed]}."""
        out = {}
        for g in _kept_subgraphs(_wdir(self.dir, w["sdir"], w["window"])):
            for n in g["nodes"]:
                if str(n.get("type", "")).lower() != "net":
                    continue
                ip, port = _split_net(n.get("attr"))
                peer = next((h for h, a in self.ips.items() if a == ip and h != host), None)
                if peer is None:
                    continue
                out.setdefault(peer, []).append({"node": n["id"], "attr": f"{ip}:{port}", "port": port, "sg": g["id"],
                                                 "sg_score": g.get("sg_score"), "role": n.get("role"),
                                                 "prob": n.get("prob"), "score": n.get("score")})
        return out

    def _open(self, host: str, start: int, w: dict):
        for peer, seeds in self._seeds(host, w).items():
            mirror = next((f for f in self.findings.values() if f["origin"] == peer and f["peer"] == host
                           and abs(f["start_ms"] - start) <= self.w_ms), None)
            if mirror is not None:                    # the peer's own red window points back: same story, one finding
                if not any(m["window"] == w["window"] for m in mirror["mirror"]):
                    mirror["mirror"].append({"window": w["window"], "start_ms": start, "sdir": w["sdir"], "seeds": seeds})
                    mirror["dirty"] = mirror["fresh"] = True
                    self.log(f"[correlate] {host} {w['window']} points back at {mirror['id']}")
                continue
            fid = f"{host}-{w['window']}-{peer}"
            if fid in self.findings:
                continue
            self.findings[fid] = {
                "id": fid, "origin": host, "peer": peer, "window": w["window"], "start_ms": start,
                "start_utc": _utc(start), "sdir": w["sdir"], "score": w["score"], "seeds": seeds, "mirror": [],
                "level": "pending", "join": None, "connections": [], "peer_windows": [], "note": None, "llm": None,
                "dirty": True, "fresh": True}
            self.log(f"[correlate] {host} {w['window']} red, {len(seeds)} NET seed(s) -> {peer}: pending")

    def _ready(self, f: dict) -> bool:
        hi = f["start_ms"] + self.w_ms
        if max(self.wins.get(f["peer"], {}), default=-1) >= hi:
            return True
        newest = max((s for w in self.wins.values() for s in w), default=-1)
        if newest >= hi + STALE_WINDOWS * self.w_ms:  # the peer stopped reporting: resolve with what is there
            f["note"] = "peer windows missing for this range"
            return True
        return False

    def _connections(self, f: dict) -> list:
        """Raw connections between origin and peer in range whose peer port is a seed's port."""
        A, P, lo, hi = f["origin"], f["peer"], f["start_ms"] - self.w_ms, f["start_ms"] + self.w_ms
        ports = {s["port"] for s in f["seeds"]} | {s["port"] for m in f["mirror"] for s in m["seeds"]}
        out = {}
        for start, b in self.conns.items():
            if not lo <= start <= hi:
                continue
            for k, r in b.items():
                if A not in r["ports"] or P not in r["ports"]:
                    continue
                if r["ports"][P] not in ports and r["ports"][A] not in ports:
                    continue                          # the second test catches a mirror seed (P's NET node = A's port)
                c = out.setdefault(k, {"proto": r["proto"], "key": r["key"], "origin_port": r["ports"][A],
                                       "peer_port": r["ports"][P], "count": 0, "first_ms": r["first_ms"],
                                       "seen_by": set(), "pids": {A: set(), P: set()}})
                c["count"] += r["count"]
                c["first_ms"] = min(c["first_ms"], r["first_ms"])
                c["seen_by"] |= r["seen_by"]
                for h, p in r["pids"].items():
                    c["pids"].setdefault(h, set()).update(p)
        return [{**c, "seen_by": sorted(c["seen_by"]), "pids": {h: sorted(p) for h, p in c["pids"].items()}}
                for c in sorted(out.values(), key=lambda c: c["first_ms"])]

    def _peer_window(self, f: dict, start: int, w: dict, targets: set, pids: set, by_ip: bool) -> dict:
        """One peer window -> the nodes around the other end of the connection, with the peer model's scores."""
        wdir = _wdir(self.dir, w["sdir"], w["window"])
        attrs = {r["node"]: r for r in _read_csv(os.path.join(wdir, "node_attrs.csv"))}
        a_ip = self.ips[f["origin"]]
        ends = set()
        for n, r in attrs.items():
            if r["node_type"].upper() != "NET":
                continue
            ip, port = _split_net(r["node_attr"])
            if f"{ip}:{port}" in targets or (by_ip and ip == a_ip):
                ends.add(n)
        procs = {n for n, r in attrs.items() if r["node_type"].upper() == "PROCESS"
                 and any(n.startswith(f"p{p}_") for p in pids)}
        edges = _read_csv(os.path.join(wdir, "edges.csv"))
        adj = {}
        for e in edges:
            adj.setdefault(e["source-id"], set()).add(e["destination-id"])
            adj.setdefault(e["destination-id"], set()).add(e["source-id"])
        scores = {r["node_uuid"]: r for r in _read_csv(os.path.join(wdir, "scores.csv"))}
        flagged = {n for n, r in scores.items() if str(r.get("flagged")).lower() == "true"}
        owners = {s for n in ends for s in adj.get(n, ())} | procs
        role = {n: "connection end" for n in ends} | {n: "owner process" for n in owners}
        for p in owners:                              # hop 1: everything the owner processes touched
            for n in adj.get(p, ()):
                role.setdefault(n, "neighbour")
        for n in [n for n in role if n in flagged]:   # hop 2 [OURS]: only through nodes the peer model flagged,
            for m in adj.get(n, ()):                  # so a common DLL does not pull in every process on the host
                role.setdefault(m, "2nd hop via flagged")
        nodes = []
        for n, why in role.items():
            r, a = scores.get(n, {}), attrs.get(n, {})
            nodes.append({"id": n, "type": (r.get("node_type") or a.get("node_type") or "").lower(),
                          "attr": a.get("node_attr", r.get("attr", "")), "why": why, "flagged": n in flagged,
                          "score": float(r["score"]) if r.get("score") else None,
                          "margin": float(r["margin"]) if r.get("margin") else None})
        nodes.sort(key=lambda x: (not x["flagged"], -(x["margin"] or -1e9)))
        cut = max(0, len(nodes) - MAX_PEER_NODES)
        nodes = nodes[:MAX_PEER_NODES]
        ids = {n["id"] for n in nodes}
        kept = [g["id"] for g in _kept_subgraphs(wdir) if ids & {n["id"] for n in g["nodes"]}]
        return {"window": w["window"], "start_ms": start, "sdir": w["sdir"], "alert": w["alert"],
                "n_flagged": sum(n["flagged"] for n in nodes), "kept_subgraphs": kept, "nodes": nodes, "cut": cut}

    def _resolve(self, f: dict):
        A, P = f["origin"], f["peer"]
        conns = self._connections(f)
        targets = {f"{self.ips[A]}:{c['origin_port']}" for c in conns}
        targets |= {s["attr"] for m in f["mirror"] for s in m["seeds"]}
        pids = {p for c in conns for p in c["pids"].get(P, [])}
        f["join"] = "5-tuple" if conns else "ip-only"  # no raw line matched: any P node with A's IP, coarse
        lo, hi = f["start_ms"] - self.w_ms, f["start_ms"] + self.w_ms
        f["peer_windows"] = [self._peer_window(f, s, w, targets, pids, not conns)
                             for s, w in sorted(self.wins.get(P, {}).items()) if lo <= s <= hi]
        f["peer_windows"] = [pw for pw in f["peer_windows"] if pw["nodes"]]
        f["connections"] = conns
        hit = any(pw["n_flagged"] or pw["kept_subgraphs"] for pw in f["peer_windows"])
        return "corroborated" if hit else "one-sided"

    def evaluate(self):
        changed, llm = [], []
        with self.lock:
            for f in self.findings.values():
                if f.pop("fresh", False):             # a new pending finding: show it now
                    changed.append(f)
                if not f.get("dirty") or not self._ready(f):
                    continue
                before = f["level"]
                level = self._resolve(f)
                if LEVELS.index(level) > LEVELS.index(f["level"]):     # a finding only ever rises
                    f["level"] = level
                f.pop("dirty", None)
                if f["level"] != before:
                    self.log(f"[correlate] {f['id']}: {before} -> {f['level']} (join {f['join']})")
                    llm.append(f["id"])
                if f not in changed:
                    changed.append(f)
            keep = max((s for w in self.wins.values() for s in w), default=0) - (STALE_WINDOWS + 3) * self.w_ms
            for start in [s for s in self.conns if s < keep]:
                del self.conns[start]
            for w in self.wins.values():
                for s in [s for s in w if s < keep]:
                    del w[s]
            if self.llm is not None:
                for fid in llm:
                    self.findings[fid]["llm"] = {"status": "pending"}
            snapshot = [{k: v for k, v in json.loads(json.dumps(f)).items() if k != "dirty"} for f in changed]
            for s in snapshot:
                self._save(s)
        for s in snapshot:
            self._emit(s)
        if self.llm is not None:
            for fid in llm:
                self.llm_jobs.put(fid)

    # ---------------------------------------------------------------- LLM
    def _llm_loop(self):
        import alg2
        while True:
            fid = self.llm_jobs.get()
            try:
                with self.lock:
                    f = json.loads(json.dumps(self.findings[fid]))
                if self.take_budget and not self.take_budget():
                    state = {"status": "skipped", "error": "daily LLM budget reached; the finding stands"}
                else:
                    doc = build_document(f, self.dir, self.ips)
                    window = {"window": fid, "start_utc": f["start_utc"], "end_utc": _utc(f["start_ms"] + self.w_ms)}
                    flagged = sum(pw["n_flagged"] for pw in f["peer_windows"]) + len(f["seeds"])
                    res = alg2.investigate_crosshost(doc, window, flagged, round(f["score"] or 0.0, 2), self.llm,
                                                     os.path.join(self.dir, "crosshost", fid))
                    state = {"status": res["verdict"], **res}
            except Exception as e:                    # investigate_crosshost catches provider errors; this is a bug guard
                import traceback
                traceback.print_exc()
                state = {"status": "error", "error": f"{type(e).__name__}: {e}"}
            finally:
                self.llm_jobs.task_done()
            with self.lock:
                f = self.findings[fid]
                f["llm"] = state
                self._save(f)
                out = json.loads(json.dumps(f))
            self._emit(out)

    # ---------------------------------------------------------------- read side
    def list(self, limit: int = 100) -> list:
        with self.lock:
            rows = sorted(self.findings.values(), key=lambda f: f["start_ms"], reverse=True)[:limit]
            return [{k: v for k, v in r.items() if k != "dirty"} for r in json.loads(json.dumps(rows))]

    def get(self, fid: str) -> dict | None:
        with self.lock:
            f = self.findings.get(fid)
            return json.loads(json.dumps(f)) if f else None

    def status(self) -> dict:
        with self.lock:
            n = {lv: sum(f["level"] == lv for f in self.findings.values()) for lv in LEVELS}
        return {"hosts": self.ips, "findings": n}

    def drain(self):
        self.llm_jobs.join()


# --------------------------------------------------------------------------- the LLM document
def _process_names(wdir: str, pids: list) -> list:
    """PIDs -> image names, from this window's node table (process nodes are "p<pid>_...")."""
    want = {f"p{p}_" for p in pids}
    return sorted({r["node_attr"].replace("/", "\\").split("\\")[-1] for r in _read_csv(os.path.join(wdir, "node_attrs.csv"))
                   if r["node_type"] == "PROCESS" and any(r["node"].startswith(w) for w in want)})


def _edges(wdir: str):
    import pandas as pd
    import train_gnn_models as tgm
    return tgm.normalise_edges(pd.read_csv(os.path.join(wdir, "edges.csv"), dtype={"source-id": str, "destination-id": str}))


def build_document(f: dict, sessions_dir: str, ips: dict, max_lines: int = MAX_DOC_LINES) -> dict:
    """Origin's kept subgraphs + the peer's related nodes + the join, one document. Join lines first, so a cut never
    drops them. Peer nodes the peer model flagged are 'anomalous' in the document, the rest 'connector'."""
    import alg2
    A, P = f["origin"], f["peer"]
    a_dir = _wdir(sessions_dir, f["sdir"], f["window"])
    when = alg2.display_time(f["start_utc"])
    net = []
    for s in f["seeds"]:
        net.append(f"[network] the anomalous subgraph on {A} (score {s['sg_score']}) holds the socket {s['attr']} on {P} "
                   f"({s['role']} node), first window {when}.")
    for c in f["connections"]:
        a_names = ", ".join(_process_names(a_dir, c["pids"].get(A, []))) or "an unresolved process"
        p_names = sorted({n for pw in f["peer_windows"] for n in
                          _process_names(_wdir(sessions_dir, pw["sdir"], pw["window"]), c["pids"].get(P, []))})
        net.append(f"[network] {a_names} on {A} ({ips.get(A)}:{c['origin_port']}) and {', '.join(p_names) or 'an unresolved process'} "
                   f"on {P} ({ips.get(P)}:{c['peer_port']}) exchanged {c['count']} {c['proto']} network events.")
    if f["join"] == "ip-only":
        net.append(f"[network] no raw network event matched the exact connection; the {P} side below is every socket "
                   f"on {P} that talks to {ips.get(A)}.")
    lines = []
    try:
        ea = _edges(a_dir)
        for sg in _kept_subgraphs(a_dir):
            lines += [f"[{A}] {ln}" for ln in alg2.serialize(sg, ea, max_lines)["lines"]]
    except (OSError, ValueError, KeyError):
        pass
    for pw in f["peer_windows"]:
        p_dir = _wdir(sessions_dir, pw["sdir"], pw["window"])
        try:
            ep = _edges(p_dir)
        except (OSError, ValueError, KeyError):
            continue
        sg = {"nodes": [{"id": n["id"], "type": n["type"], "attr": n["attr"],
                         "role": "anomalous" if n["flagged"] else "connector"} for n in pw["nodes"]]}
        lines += [f"[{P}] {ln}" for ln in alg2.serialize(sg, ep, max_lines)["lines"]]
        for g in _kept_subgraphs(p_dir):
            if g["id"] in pw["kept_subgraphs"]:
                lines += [f"[{P}] {ln}" for ln in alg2.serialize(g, ep, max_lines)["lines"]]
    keep = list(dict.fromkeys(net + lines))
    cut = max(0, len(keep) - max_lines)
    keep = keep[:max_lines]
    return {"text": "".join(f"{ln} \n" for ln in keep), "lines": keep, "truncated": cut,
            "n_events": sum(c["count"] for c in f["connections"])}


# --------------------------------------------------------------------------- selftest
def selftest():
    """'It runs' + invariants on synthetic windows and flows. Never evidence that real cross-host movement is detected."""
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
    assert a["key"] == b["key"] == "h0>h1/tcp/445" and a["conn"] == b["conn"], (a, b)
    assert (a["lport"], a["rport"], b["lport"], b["rport"]) == (51000, 445, 445, 51000)
    u = parse_flow(ev(udp_r, "10.0.0.10", "10.0.0.11", 51000, 53), "h1", ips)
    assert u["key"] == "h0>h1/udp/53", u
    assert parse_flow(ev(udp_r, "10.0.0.10", "10.0.0.255", 138, 138), "h1", ips) is None, "broadcast kept"
    assert parse_flow(ev(udp_r, "10.0.0.10", "224.0.0.251", 5353, 5353), "h1", ips) is None, "multicast kept"
    assert parse_flow(ev(tcp_s, "10.0.0.10", "8.8.8.8", 51000, 443), "h0", ips) is None, "unmonitored peer kept"
    assert parse_flow(ev("KERNEL_NETWORK_TASK_TCPIP/connectionattempted", "10.0.0.10", "10.0.0.11", 1, 2), "h0", ips) is None
    assert parse_flow(ev(tcp_s, "10.0.0.10", "10.0.0.11", 50000, 50001), "h0", ips)["key"] == "h0>h1/tcp/dyn"
    assert parse_flow(ev(udp_s, "10.0.0.10", "10.0.0.11", 138, 138), "h0", ips)["key"] == "h0>h1/udp/138"
    assert _split_net("10.0.0.11:445") == ("10.0.0.11", 445) and _split_net("x") == (None, None)
    assert _split_net("-1,707,025,385:-17,663")[0] == "23.220.64.154", "SilkETW int form not decoded"
    assert "several Windows hosts" in alg2.CROSSHOST_TRIAGE_SYSTEM and "[network]" in alg2.CROSSHOST_TRIAGE_SYSTEM
    assert "one Windows 10 host" in alg2.TRIAGE_SYSTEM, "the per-host prompt must stay as it was"

    base = 1_791_000_000_000                         # multiple of 5 min
    w = 300_000

    def rec(k, alert=False, flagged=0, score=0.0):
        return {"window": f"w{k}", "start_ms": base + k * w, "status": "scored", "alert": alert, "flagged": flagged,
                "max_sg_score": score}

    def window(d, host, k, nodes, edges, flagged=(), kept=None):
        """nodes: {id: (TYPE, attr)}; edges: [(src, dst, type)]; kept: ids of one kept subgraph (+ one not kept)."""
        wd = os.path.join(d, f"s-{host}", "windows", f"w{k}")
        os.makedirs(wd, exist_ok=True)
        t = base + k * w + 1000
        with open(os.path.join(wd, "edges.csv"), "w") as fh:
            fh.write("source-id,source-type,destination-id,destination-type,edge-type,timestamp\n")
            fh.writelines(f"{s},{nodes[s][0]},{x},{nodes[x][0]},{e},{t + i}\n" for i, (s, x, e) in enumerate(edges))
        with open(os.path.join(wd, "node_attrs.csv"), "w") as fh:
            fh.write("node,node_type,node_attr\n" + "".join(f"{n},{ty},{at}\n" for n, (ty, at) in nodes.items()))
        with open(os.path.join(wd, "scores.csv"), "w") as fh:
            fh.write("node_uuid,node_type,score,tau,margin,flagged,attr\n" +
                     "".join(f"{n},{ty.lower()},0.1,0.0,{0.5 if n in flagged else -0.5},{n in flagged},{at}\n"
                             for n, (ty, at) in nodes.items()))
        sgs = []
        if kept:
            sgs.append({"id": 0, "kept": True, "sg_score": 12.0, "level": "Moderate", "nodes": [
                {"id": n, "type": nodes[n][0].lower(), "attr": nodes[n][1],
                 "role": "anomalous" if n in flagged else "connector", "prob": 0.9, "score": 0.1} for n in kept]})
        sgs.append({"id": 1, "kept": False, "sg_score": 1.0, "level": "Low", "nodes": [
            {"id": "nx", "type": "net", "attr": "10.0.0.11:3389", "role": "anomalous"}]})
        json.dump({"subgraphs": sgs}, open(os.path.join(wd, "subgraphs.json"), "w"))

    # origin h0, window 1: tool0.exe (pid 700) in a kept subgraph with the socket 10.0.0.11:445 and an internet socket
    h0n = {"p700_1": ("PROCESS", r"c:\x\tool0.exe"), "n445": ("NET", "10.0.0.11:445"), "nweb": ("NET", "8.8.8.8:443"),
           "f1": ("FILE", r"c:\x\out.bin")}
    h0e = [("p700_1", "n445", "Send"), ("p700_1", "nweb", "Send"), ("p700_1", "f1", "Write")]
    # peer h1: lsass-like pid 900 owns the other end 10.0.0.10:51001, touches a dll every process loads and a dropped file
    h1n = {"p900_1": ("PROCESS", r"c:\w\svc.exe"), "nend": ("NET", "10.0.0.10:51001"), "fdll": ("FILE", r"c:\w\common.dll"),
           "fdrop": ("FILE", r"c:\w\drop.exe"), "p950_1": ("PROCESS", r"c:\w\other.exe"),
           "p960_1": ("PROCESS", r"c:\w\runner.exe"), "nold": ("NET", "10.0.0.10:60000")}
    h1e = [("p900_1", "nend", "Recv"), ("p900_1", "fdll", "Load"), ("p900_1", "fdrop", "Write"),
           ("p950_1", "fdll", "Load"), ("p960_1", "fdrop", "Read")]

    with tempfile.TemporaryDirectory() as d:
        window(d, "h0", 0, h0n, h0e)                                       # green, no kept subgraph
        window(d, "h0", 1, h0n, h0e, flagged={"p700_1", "f1"}, kept=["p700_1", "n445", "nweb", "f1"])
        for k in (0, 1, 2):
            window(d, "h1", k, h1n, h1e)                                   # peer: nothing flagged
        seen = []
        c = Correlator(ips, d, w, llm=alg2.MockLLM(alg2.rule_mock_responder), take_budget=lambda: True, log=lambda *_: None)
        c.listeners.append(seen.append)

        # a connection alone is never a finding, nor is a green window
        c.add_line("h0", ev(tcp_s, "10.0.0.10", "10.0.0.11", 51001, 445), base + w + 2000)
        c.add_line("h1", ev(tcp_r, "10.0.0.11", "10.0.0.10", 445, 51001, pid=900), base + w + 2100)
        c.add_line("h1", ev(tcp_r, "10.0.0.11", "10.0.0.10", 3389, 60000, pid=999), base + w + 2200)  # other conn
        c.on_window("h0", rec(0), "s-h0")
        c.on_window("h1", rec(0), "s-h1")
        assert c.list() == [], c.list()

        # red window: one seed (n445, not the internet socket nor the not-kept 3389 one), pending until h1 passes t+w
        c.on_window("h0", rec(1, True, 2, 12.0), "s-h0")
        got = c.list()
        assert len(got) == 1 and got[0]["level"] == "pending" and got[0]["peer"] == "h1", got
        assert [s["node"] for s in got[0]["seeds"]] == ["n445"], got[0]["seeds"]
        c.on_window("h1", rec(1), "s-h1")
        assert c.list()[0]["level"] == "pending"
        c.on_window("h1", rec(2), "s-h1")
        c.drain()
        f = c.list()[0]
        assert f["level"] == "one-sided" and f["join"] == "5-tuple", f
        assert f["llm"]["status"] in ("attack", "false_positive", "uncertain"), f["llm"]
        assert [x["origin_port"] for x in f["connections"]] == [51001] and f["connections"][0]["pids"]["h1"] == [900]
        ids = {n["id"]: n["why"] for n in f["peer_windows"][0]["nodes"]}
        assert ids.get("nend") == "connection end" and ids.get("p900_1") == "owner process", ids
        assert "fdrop" in ids and "fdll" in ids and "p950_1" not in ids and "nold" not in ids, ids

        # the LLM document: join lines first, both hosts tagged
        doc = build_document(f, d, ips)
        assert doc["lines"][0].startswith("[network] ") and "10.0.0.11:445" in doc["lines"][0], doc["lines"][:3]
        assert any("tool0.exe" in x and "svc.exe" in x for x in doc["lines"] if x.startswith("[network]")), doc["lines"][:4]
        assert any(x.startswith("[h0] ") for x in doc["lines"]) and any(x.startswith("[h1] ") for x in doc["lines"]), doc
        assert os.path.exists(os.path.join(d, "crosshost", f["id"], "doc.txt"))
        assert any(e["type"] == "crosshost" for e in seen)

        # the peer's own red window points back at the same connection: no mirror finding, the first one rises
        window(d, "h1", 2, h1n, h1e, flagged={"nend", "fdrop"}, kept=["p900_1", "nend", "fdrop"])
        c.on_window("h1", rec(2, True, 2, 11.0), "s-h1")
        c.drain()
        got = c.list()
        assert len(got) == 1 and got[0]["level"] == "corroborated" and got[0]["mirror"][0]["window"] == "w2", got
        p2 = [pw for pw in got[0]["peer_windows"] if pw["window"] == "w2"][0]
        assert p2["kept_subgraphs"] == [0] and p2["n_flagged"] == 2, p2
        ids = {n["id"] for n in p2["nodes"]}
        assert "p960_1" in ids, "2nd hop via the flagged dropped file missing"

        # a restart reloads; a pending finding cannot finish after a restart and says so
        c2 = Correlator(ips, d, w, log=lambda *_: None)
        assert len(c2.list()) == 1 and c2.list()[0]["level"] == "corroborated"

        # no raw line matched (collector gap): ip-only join, budget spent -> the finding stands, LLM skipped
        d2 = os.path.join(d, "b")
        window(d2, "h0", 1, h0n, h0e, flagged={"p700_1"}, kept=["p700_1", "n445"])
        for k in (1, 2):
            window(d2, "h1", k, h1n, h1e, flagged={"p950_1"} if k == 1 else ())
        c3 = Correlator(ips, d2, w, llm=alg2.MockLLM(alg2.rule_mock_responder), take_budget=lambda: False, log=lambda *_: None)
        c3.on_window("h0", rec(1, True, 1, 11.0), "s-h0")
        c3.on_window("h1", rec(1), "s-h1")
        c3.on_window("h1", rec(2), "s-h1")
        c3.drain()
        got = c3.list()[0]
        assert got["join"] == "ip-only" and got["llm"]["status"] == "skipped", got
        ids = {n["id"] for pw in got["peer_windows"] for n in pw["nodes"]}
        assert {"nend", "nold"} <= ids and got["level"] == "one-sided", (ids, got["level"])   # p950 not reached via fdll

        # the peer went silent: resolve after STALE_WINDOWS with a note
        d3 = os.path.join(d, "c")
        window(d3, "h0", 1, h0n, h0e, flagged={"p700_1"}, kept=["p700_1", "n445"])
        c4 = Correlator(ips, d3, w, log=lambda *_: None)
        c4.on_window("h0", rec(1, True, 1, 11.0), "s-h0")
        for k in range(2, 3 + STALE_WINDOWS):
            window(d3, "h0", k, h0n, h0e)
            c4.on_window("h0", rec(k), "s-h0")
        got = c4.list()[0]
        assert got["level"] == "one-sided" and got["note"] and got["peer_windows"] == [], got
    assert load_hosts("/nonexistent") == {}
    two_engines()
    print("[SELFTEST OK] TCP/UDP address parsing, broadcast + multicast + unmonitored peers dropped, no finding without a "
          "red window, NET seeds from kept subgraphs only, pending until the peer closes, 5-tuple join to the peer's "
          "process, 2nd hop only via flagged nodes, one-sided -> corroborated, mirror merges, ip-only fallback, stale "
          "peer, LLM document holds both hosts, restart reloads, budget respected, two engines with different models, "
          "one-host results unchanged")


def two_engines():
    """Two real Engines with different tiny models feed one Correlator, and a host's windows match a plain Engine's."""
    import tempfile
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
        cor = Correlator(ips, sess, 300_000, log=lambda *_: None)
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
        conns = [r for b in cor.conns.values() for r in b.values()]
        assert any(r["key"] == "h0>h1/tcp/445" and r["seen_by"] == {"h0", "h1"} for r in conns), conns
        red = {h for h, e in engines.items() for w in e.windows() if w.get("alert")}
        assert all(f["origin"] in red for f in cor.list()), "a finding without a red window"

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
