"""sysmon2nodlink.py - convert Sysmon-for-Linux event captures into the OCR-APT / NODLINK CSV schema.

This is the Linux counterpart of etw2nodlink.py. It emits the SAME output contract, so everything
downstream (etwwindow.py, encode_to_features.py, train_gnn_models.py, score_window.py) is unchanged:

Input  : <raw-root>/<split>/<run>/*.log   (one Sysmon-for-Linux event per line; see INPUT FORMAT below)
Output : <out>/<graph>_<split>_edges.csv       source-id,source-type,destination-id,destination-type,edge-type,timestamp
         <out>/<graph>_<split>_node_attrs.csv  node,node_type,node_attr
         <out>/<graph>_<split>_summary.json    counts, unmapped event ids, unresolved lookups

Stdlib only (xml.etree is stdlib): runs on any Python >= 3.8 on the trainer. Edge timestamps are epoch
milliseconds, exactly as etw2nodlink.py produces them.

INPUT FORMAT  ([INFERENCE] confirm against real output before trusting on live data):
  Sysmon-for-Linux ("sysmon") logs each event to syslog as the Windows Sysmon event XML, one event per
  syslog message. Shipped verbatim by Alloy, a raw line looks like:

    <syslog prefix> sysmon: <Event ...><System>...<EventID>1</EventID>...</System>
                            <EventData><Data Name='UtcTime'>2026-10-19 05:00:00.123</Data>
                            <Data Name='ProcessId'>4321</Data><Data Name='Image'>/usr/bin/bash</Data>...
                            </EventData></Event>

  We ignore the syslog prefix (everything before the first "<Event"), parse the XML, read <EventID> and
  the EventData Data Name->value pairs. A line with no "<Event" (plain syslog noise) is counted bad and
  skipped. Linux ProcessId is the host PID; Sysmon UtcTime is the event time in UTC.

Edge mapping (Sysmon-for-Linux emits a subset of Windows Sysmon; see docs/DEVIATIONS.md):
  EventID 1  ProcessCreate   -> Start  (parent -> child PROCESS)
  EventID 5  ProcessTerminate-> (drops the pid so a reused PID is not confused)
  EventID 11 FileCreate      -> Write  (PROCESS -> FILE, TargetFilename)
  EventID 9  RawAccessRead   -> Read   (PROCESS -> FILE, Device)
  EventID 3  NetworkConnect  -> Send   (PROCESS -> NET, DestinationIp:DestinationPort)
  There is no ImageLoad event on Linux Sysmon, so NO "Load" edges; no distinct recv, so network is
  Send-only; file I/O is approximated from Create/RawAccessRead, not a Read/Write stream. The Linux
  graph is therefore sparser than the Windows one - do not compare densities/scores across the two.
"""
import argparse, csv, glob, hashlib, heapq, json, os, re, sys, tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

EDGE_HEADER = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
NODE_HEADER = ["node", "node_type", "node_attr"]

# Edge direction for process<->object edges. "fwd" = process -> object (matches etw2nodlink.py).
DIRECTION = {"Load": "fwd", "Read": "fwd", "Write": "fwd", "Send": "fwd", "Recv": "fwd"}

# Drop edges whose process is the log pipeline itself: Sysmon writes to syslog, rsyslog writes the raw
# file, Alloy reads it -> a feedback loop of FileCreate/RawAccessRead on these images. Same idea as
# etw2nodlink.py excluding silkservice/alloy. Basenames, lowercased, any suffix.
EXCLUDE_IMAGES = {"alloy", "rsyslogd", "syslog-ng", "sysmon"}

# Sysmon EventID -> internal kind. (ProcessTerminate handled for pid bookkeeping only.)
EVENT_KIND = {"1": "pstart", "5": "pstop", "3": "net", "11": "fcreate", "9": "rawread"}

_EVENT_RE = re.compile(r"<Event\b.*</Event>", re.DOTALL)
# Sysmon XML carries a default namespace on <Event>; strip it so tags match without a prefix.
_NS_RE = re.compile(r"\sxmlns(:\w+)?=['\"][^'\"]*['\"]")


def num(v):
    """'4,321' / '0x10E1' / 4321 -> int; None if unparseable (mirrors etw2nodlink.num)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(float(s))
    except ValueError:
        return None


def first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def ts_ms(s):
    """Sysmon UtcTime -> epoch ms. Accepts 'YYYY-MM-DD HH:MM:SS.fff' (UTC, no offset) and ISO 8601."""
    s = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)                 # handles both space and 'T' separators (py>=3.11),
    except ValueError:                                 # and most offsets
        dt = datetime.strptime(s[:23], "%Y-%m-%d %H:%M:%S.%f")   # fallback: millisecond UtcTime
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)           # Sysmon UtcTime is UTC by definition
    return int(dt.timestamp() * 1000)


def parse_event(line):
    """One raw syslog line -> (event_id, utc_time, {data name: value}) or None if it carries no event."""
    m = _EVENT_RE.search(line)
    if not m:
        return None
    xml = _NS_RE.sub("", m.group(0))                   # drop namespaces so .find('System') works
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    eid = root.findtext("System/EventID")
    data = {}
    for d in root.findall("EventData/Data"):
        name = d.get("Name")
        if name:
            data[name.lower()] = (d.text or "").strip()
    return eid, data.get("utctime"), data


def read_events(path, stats):
    """Yield (epoch_ms, event_id, data) from one capture file. Truncated/garbage lines are skipped."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parsed = parse_event(line)
            if parsed is None:
                if line.strip():
                    stats["bad_lines"][os.path.basename(path)] += 1
                continue
            eid, utc, data = parsed
            try:
                t = ts_ms(utc)
            except (ValueError, TypeError):
                stats["bad_lines"][os.path.basename(path)] += 1
                continue
            yield t, eid, data


class Graph:
    def __init__(self, writer, stats):
        self.w, self.st = writer, stats
        self.nodes = {}    # node id -> [node_type, node_attr]
        self.used = set()  # node ids referenced by a written edge

    def new_run(self, run):
        # PIDs are only meaningful inside one capture session (reused across reboots).
        self.run, self.pid2proc = run, {}

    # ---- nodes ----
    def path(self, p):
        """POSIX paths need no DOS-device translation (contrast etw2nodlink.path); just normalise case-free."""
        return str(p).strip() if p else ""

    def add_node(self, nid, ntype, attr):
        cur = self.nodes.get(nid)
        if cur is None:
            self.nodes[nid] = [ntype, attr or ""]
        elif not cur[1] and attr:
            cur[1] = attr
        return nid

    def proc(self, pid, name_hint=""):
        if pid is None:
            return None
        nid = self.pid2proc.get(pid)
        if nid is None:  # process started before the capture: we never saw its ProcessCreate
            nid = self.pid2proc[pid] = "p%d_pre_%s" % (pid, self.run)
        return self.add_node(nid, "PROCESS", name_hint)

    def obj(self, prefix, ntype, key):
        return self.add_node(prefix + hashlib.sha1(key.encode()).hexdigest()[:16], ntype, key)

    # ---- edges ----
    def edge(self, proc_id, other_id, etype, t):
        base = re.split(r"[\\/]", self.nodes[proc_id][1])[-1].lower()
        base = re.sub(r"\.(exe|bin)$", "", base)
        if base in EXCLUDE_IMAGES:
            self.st["excluded"][etype] += 1
            return
        if etype == "Start" or DIRECTION[etype] == "fwd":
            src, dst = proc_id, other_id
        else:
            src, dst = other_id, proc_id
        self.w.writerow([src, self.nodes[src][0], dst, self.nodes[dst][0], etype, t])
        self.used.update((src, dst))
        self.st["edges"][etype] += 1

    # ---- dispatch ----
    def handle(self, t, eid, data):
        kind = EVENT_KIND.get(eid)
        if kind is None:
            self.st["unmapped"]["EventID " + str(eid)] += 1
            return
        self.st["events"][kind] += 1
        getattr(self, "on_" + kind)(t, data)

    def on_pstart(self, t, d):
        cpid, ppid = num(d.get("processid")), num(d.get("parentprocessid"))
        if cpid is None:
            self.st["unresolved"]["pstart"] += 1
            return
        parent = self.proc(ppid, self.path(d.get("parentimage")))
        if parent is None:
            self.st["unresolved"]["pstart"] += 1
            return
        child = self.add_node("p%d_%d" % (cpid, t), "PROCESS", self.path(d.get("image")))
        self.pid2proc[cpid] = child
        self.edge(parent, child, "Start", t)

    def on_pstop(self, t, d):
        self.pid2proc.pop(num(d.get("processid")), None)

    def _file_io(self, t, d, etype, key):
        pid, p = num(d.get("processid")), self.path(key)
        proc = self.proc(pid, self.path(d.get("image")))
        if not p or proc is None:
            self.st["unresolved"][etype] += 1
            return
        self.edge(proc, self.obj("f", "FILE", p), etype, t)

    def on_fcreate(self, t, d):
        self._file_io(t, d, "Write", d.get("targetfilename"))

    def on_rawread(self, t, d):
        self._file_io(t, d, "Read", d.get("device"))

    def on_net(self, t, d):
        pid = num(d.get("processid"))
        daddr, dport = d.get("destinationip"), num(d.get("destinationport"))
        proc = self.proc(pid, self.path(d.get("image")))
        if proc is None or not daddr:
            self.st["unresolved"]["Send"] += 1
            return
        self.edge(proc, self.obj("n", "NET", "%s:%s" % (daddr, dport)), "Send", t)


def convert(raw_root, split, graph, out_dir):
    split_dir = os.path.join(raw_root, split)
    runs = sorted(d for d in glob.glob(os.path.join(split_dir, "*")) if os.path.isdir(d))
    if not runs:
        raise SystemExit("no capture runs under " + split_dir)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, "%s_%s" % (graph, split))
    st = {k: Counter() for k in ("events", "edges", "excluded", "unresolved", "unmapped", "bad_lines")}

    # Write to .tmp and rename at the end: a crashed run never leaves a half CSV that looks complete.
    with open(base + "_edges.csv.tmp", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(EDGE_HEADER)
        g = Graph(w, st)
        for run in runs:
            g.new_run(os.path.basename(run))
            # one file per run is typical (sysmon.log), but merge any by time so PID lookups see order
            streams = [((t, eid, data) for t, eid, data in read_events(f, st))
                       for f in sorted(glob.glob(os.path.join(run, "*")))
                       if os.path.isfile(f) and not f.endswith(".meta")]
            for t, eid, data in heapq.merge(*streams, key=lambda x: x[0]):
                g.handle(t, eid, data)

    with open(base + "_node_attrs.csv.tmp", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(NODE_HEADER)
        for nid in sorted(g.used):
            ntype, attr = g.nodes[nid]
            w.writerow([nid, ntype, attr or "unknown"])

    os.replace(base + "_edges.csv.tmp", base + "_edges.csv")
    os.replace(base + "_node_attrs.csv.tmp", base + "_node_attrs.csv")
    node_types = Counter(g.nodes[n][0] for n in g.used)
    summary = {"graph": graph, "split": split, "source": "sysmon-for-linux",
               "runs": [os.path.basename(r) for r in runs],
               "nodes": dict(node_types), "edges": dict(st["edges"]),
               "events": dict(st["events"]), "excluded": dict(st["excluded"]),
               "unresolved": dict(st["unresolved"]), "bad_lines": dict(st["bad_lines"]),
               "unmapped_top": dict(st["unmapped"].most_common(40))}
    with open(base + "_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    return summary


def selftest():
    """Synthetic Sysmon-for-Linux capture -> check the expected edges, 1 exclusion, 1 unmapped event."""
    def ev(eid, utc, **data):
        datas = "".join("<Data Name='%s'>%s</Data>" % (k, v) for k, v in data.items())
        return ("2026-10-19T05:00:00+07:00 ubuntu-target sysmon: "
                "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"
                "<System><Provider Name='Linux-Sysmon'/><EventID>%s</EventID></System>"
                "<EventData><Data Name='UtcTime'>%s</Data>%s</EventData></Event>" % (eid, utc, datas))
    lines = [
        # explorer-ish parent (pid 100) spawns bash (pid 4321)
        ev("1", "2026-10-19 05:00:00.000", ProcessId="4,321", ParentProcessId="100",
           Image="/usr/bin/bash", ParentImage="/usr/bin/login"),
        ev("11", "2026-10-19 05:00:01.000", ProcessId="4321", Image="/usr/bin/bash",
           TargetFilename="/tmp/x.txt"),                                   # -> Write
        ev("9", "2026-10-19 05:00:01.500", ProcessId="4321", Image="/usr/bin/bash",
           Device="/dev/sda1"),                                           # -> Read
        ev("3", "2026-10-19 05:00:03.000", ProcessId="4321", Image="/usr/bin/bash",
           DestinationIp="93.184.216.34", DestinationPort="443"),         # -> Send
        ev("11", "2026-10-19 05:00:04.000", ProcessId="999", Image="/usr/sbin/rsyslogd",
           TargetFilename="/var/lib/etwlab/raw/train/run1/sysmon.log"),    # excluded (shipper feedback)
        ev("7", "2026-10-19 05:00:05.000", ProcessId="4321", Image="/usr/bin/bash",
           ImageLoaded="/lib/x.so"),                                       # unmapped (no Load on Linux)
        "Oct 19 05:00:06 ubuntu-target systemd[1]: plain syslog noise, no event",  # bad line
    ]
    with tempfile.TemporaryDirectory() as tmp:
        run = os.path.join(tmp, "raw", "train", "run1")
        os.makedirs(run)
        with open(os.path.join(run, "sysmon.log"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        s = convert(os.path.join(tmp, "raw"), "train", "SelfTest", os.path.join(tmp, "out"))
        with open(os.path.join(tmp, "out", "SelfTest_train_edges.csv"), encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        with open(os.path.join(tmp, "out", "SelfTest_train_node_attrs.csv"), encoding="utf-8") as fh:
            attrs = fh.read()
    ok = (s["edges"] == {"Start": 1, "Write": 1, "Read": 1, "Send": 1}
          and s["excluded"] == {"Write": 1}
          and "/usr/bin/bash" in attrs
          and sum(s["unmapped_top"].values()) == 1 and sum(s["bad_lines"].values()) == 1
          and rows[0] == EDGE_HEADER and len(rows) == 5)
    print("selftest", "OK" if ok else "FAILED", json.dumps({"edges": s["edges"], "excluded": s["excluded"],
          "unmapped": s["unmapped_top"], "bad_lines": s["bad_lines"]}))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sysmon-for-Linux XML -> OCR-APT/NODLINK CSVs")
    ap.add_argument("--raw-root")
    ap.add_argument("--split", choices=["train", "test"])
    ap.add_argument("--graph", default="SimulatedU22")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.raw_root and a.split and a.out):
        ap.error("--raw-root, --split and --out are required")
    s = convert(a.raw_root, a.split, a.graph, a.out)
    print(json.dumps({k: s[k] for k in ("runs", "nodes", "edges", "unresolved", "excluded")}, indent=1))
    return 0 if s["edges"] else 2


if __name__ == "__main__":
    sys.exit(main())
