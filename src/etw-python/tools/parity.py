#!/usr/bin/env python3
"""parity.py - compare an etwcollect capture with a SilkService capture of the SAME minutes (migration check).

Both collectors can record at the same time: ETW lets several sessions enable one provider. Run SilkService
(etwctl, Collector=silketw) and `etwcollect run --no-push --out-dir C:\\lab-out\\etw\\parity\\test\\p1` together
for a few minutes, stop both, then:

    python tools/parity.py --silk C:\\lab-out\\etw\\raw\\test\\<run> --ours C:\\lab-out\\etw\\parity\\test\\p1

Events are paired per (provider, ThreadID) in time order, within --tol-ms: both sessions see the same events
in the same order per thread, but TraceEvent derives its timestamps its own way (sub-ms differences), so an
exact timestamp key does not match. For matched events it compares the fields the converter reads,
after undoing SilkETW's formatting:
    "4,096" -> 4096 · daddr "-1,707,025,385" (little-endian int32) -> 23.220.64.154 ·
    dport "-17,663" (network-order u16 read as int16) -> 443 · knet owner PID: SilkETW `_PID` vs ours `PID`.
Unmatched events are grouped by process name: SilkService's own events (and Alloy's) are expected on the
SilkETW side only, ours should hold nothing SilkETW lacks except the window edges.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from etwcollect.loki import ts_ns  # noqa: E402

FIELDS = {"kproc": ["ProcessID", "ParentProcessID", "ImageName"],
          "kfile": ["FileObject", "FileKey", "FileName", "IOSize"],
          "knet": ["PID", "daddr", "dport", "size"]}


def num(v):
    s = str(v).replace(",", "").strip()
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        return s.lower()


def silk_norm(stem: str, d: dict) -> dict:
    out = {}
    for f in FIELDS[stem]:
        src = "_PID" if (stem == "knet" and f == "PID") else f
        v = d.get(src)
        if v is None:
            continue
        if stem == "knet" and f == "daddr" and re.fullmatch(r"-?[\d,]+", str(v)):
            v = ".".join(str(b) for b in struct.pack("<i", int(str(v).replace(",", ""))))
        elif stem == "knet" and f == "dport" and re.fullmatch(r"-?[\d,]+", str(v)):
            v = struct.unpack(">H", struct.pack("<h", int(str(v).replace(",", ""))))[0]
        out[f] = num(v) if f not in ("daddr", "FileName", "ImageName") else str(v).lower()
    return out


def ours_norm(stem: str, d: dict) -> dict:
    return {f: (num(d[f]) if f not in ("daddr", "FileName", "ImageName") else str(d[f]).lower())
            for f in FIELDS[stem] if f in d}


def first_ts(path: str) -> int | None:
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            try:
                return ts_ns(json.loads(line)["TimeStamp"])
            except (ValueError, KeyError):
                continue
    return None


def load(path: str, lo: int, hi: int) -> list[dict]:
    """Events with lo <= ts <= hi only (streamed: captures can be hundreds of MB)."""
    out = []
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
                if lo <= ts_ns(e["TimeStamp"]) <= hi:
                    x = e.get("XmlEventData") or {}
                    keep = set(FIELDS["kproc"] + FIELDS["kfile"] + FIELDS["knet"] + ["_PID"])
                    out.append({k: e.get(k) for k in ("ThreadID", "TimeStamp", "EventName", "ProcessID", "ProcessName")}
                               | {"XmlEventData": {k: v for k, v in x.items() if k in keep}})
            except (ValueError, KeyError):
                pass
    return out


def compare(silk_dir: str, ours_dir: str, seconds: float = 60, skip: float = 5, tol_ms: float = 5) -> dict:
    report = {}
    for stem in ("kproc", "kfile", "knet"):
        sp, op = os.path.join(silk_dir, stem + ".json"), os.path.join(ours_dir, stem + ".json")
        if not (os.path.exists(sp) and os.path.exists(op)):
            report[stem] = {"missing": [p for p in (sp, op) if not os.path.exists(p)]}
            continue
        f1, f2 = first_ts(sp), first_ts(op)
        if f1 is None or f2 is None:
            report[stem] = {"empty": [p for p, f in ((sp, f1), (op, f2)) if f is None]}
            continue
        lo = max(f1, f2) + int(skip * 1e9)                 # both running, past the start-up
        hi = lo + int(seconds * 1e9)
        silk, ours = load(sp, lo, hi), load(op, lo, hi)
        tol = int(tol_ms * 1e6)
        threads = defaultdict(lambda: ([], []))
        for e in silk:
            threads[int(e.get("ThreadID", 0))][0].append(e)
        for e in ours:
            threads[int(e.get("ThreadID", 0))][1].append(e)
        pairs, silk_only, used = [], Counter(), set()
        offsets = []
        for a_list, b_list in threads.values():
            a_list.sort(key=lambda e: ts_ns(e["TimeStamp"]))
            b_list.sort(key=lambda e: ts_ns(e["TimeStamp"]))
            i = j = 0
            while i < len(a_list) and j < len(b_list):
                ta, tb = ts_ns(a_list[i]["TimeStamp"]), ts_ns(b_list[j]["TimeStamp"])
                if abs(ta - tb) <= tol:
                    pairs.append((a_list[i], b_list[j]))
                    offsets.append(tb - ta)
                    used.add(id(b_list[j]))
                    i, j = i + 1, j + 1
                elif ta < tb:
                    silk_only[a_list[i].get("ProcessName", "?")] += 1
                    i += 1
                else:
                    j += 1
            for e in a_list[i:]:
                silk_only[e.get("ProcessName", "?")] += 1
        matched, mism, names, pname = 0, Counter(), Counter(), Counter()
        examples = defaultdict(list)
        for e, o in pairs:
            matched += 1
            if e.get("EventName") != o.get("EventName"):
                names[(e.get("EventName"), o.get("EventName"))] += 1
            if e.get("ProcessID") != o.get("ProcessID"):
                mism["header ProcessID"] += 1
            if e.get("ProcessName") != o.get("ProcessName") and e.get("ProcessName") != "N/A":
                pname[(e.get("ProcessName"), o.get("ProcessName"))] += 1
            a, b = silk_norm(stem, e.get("XmlEventData") or {}), ours_norm(stem, o.get("XmlEventData") or {})
            for f in FIELDS[stem]:
                if f in a and a.get(f) != b.get(f):
                    mism[f] += 1
                    if len(examples[f]) < 3:
                        examples[f].append({"silk": a.get(f), "ours": b.get(f), "event": e.get("EventName")})
        ours_only = Counter(o.get("ProcessName", "?") for o in ours if id(o) not in used)
        offsets.sort()
        report[stem] = {"window_utc": [lo, hi], "silk": len(silk), "ours": len(ours), "matched": matched,
                        "ts_offset_us_median": round(offsets[len(offsets) // 2] / 1000, 1) if offsets else None,
                        "matched_of_ours": round(matched / len(ours), 4) if ours else None,
                        "field_mismatch": dict(mism), "examples": dict(examples),
                        "event_name_diff": {f"{k[0]} | {k[1]}": v for k, v in names.most_common(10)},
                        "process_name_diff": {f"{k[0]} | {k[1]}": v for k, v in pname.most_common(10)},
                        "silk_only_by_process": dict(silk_only.most_common(10)),
                        "ours_only_by_process": dict(ours_only.most_common(10))}
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--silk", required=True, help="SilkService run folder (kproc/kfile/knet.json)")
    ap.add_argument("--ours", required=True, help="etwcollect run folder")
    ap.add_argument("--json", help="also write the report here")
    ap.add_argument("--seconds", type=float, default=60, help="compare this many seconds of event time")
    ap.add_argument("--skip", type=float, default=5, help="start this many seconds after both began")
    ap.add_argument("--tol-ms", type=float, default=5, help="max timestamp difference of a pair")
    a = ap.parse_args()
    rep = compare(a.silk, a.ours, a.seconds, a.skip, a.tol_ms)
    text = json.dumps(rep, indent=1, default=str)
    print(text)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
