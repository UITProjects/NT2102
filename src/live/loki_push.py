#!/usr/bin/env python3
"""loki_push.py - push a recorded SilkETW run folder into Loki in the same shape Alloy writes. Test feed for
the Ubuntu side (pull, build, live) when no Windows VM is running; also a no-VM demo path.

Alloy on the VM (roles/win_alloy/templates/config.alloy.j2) and this script must agree on:
    labels               job="silketw", host, split, run, provider (= file stem: kproc|kfile|knet)
    structured metadata  event (EventName), pid (ProcessID), process (ProcessName)
    timestamp            the line's TimeStamp (event time, ns)
    line                 unchanged (except with --shift-to-now, see below)
plus one job="etw-meta" line per run holding the dos map (etwctl writes dosmap.meta on the VM).

    # original timestamps, as fast as Loki takes it (pull/build tests)
    uv run python src/live/loki_push.py --dir src/out/etw/experiments/<id>/raw/train/<run> --split train

    # live test: re-time the run so it ENDS now, then feed it at 10x (the live server's LokiSource follows)
    uv run python src/live/loki_push.py --dir .../raw/train/<run> --split test --run live-replay1 \\
        --shift-to-now --speed 10

--shift-to-now rewrites each line's "TimeStamp" (as UTC, 7 fractional digits) because the converter reads
event time from the line, not from Loki. Only this test feed does that; Alloy never rewrites lines.

    uv run python src/live/loki_push.py --selftest
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pipeline"))
sys.path.insert(0, HERE)
from etwship import infer_dos_map  # noqa: E402
from lokiclient import Loki, ts_ns  # noqa: E402

_TS = re.compile(r'("TimeStamp"\s*:\s*")([^"]+)(")')
BATCH_BYTES = 1 << 20          # like Alloy's batch_size (1 MiB); Loki's gRPC hop caps a push at its max msg size


def meta(line):
    """Alloy's stage.json + stage.structured_metadata, for one line."""
    try:
        ev = json.loads(line)
    except ValueError:
        return {}
    out = {"event": ev.get("EventName"), "pid": ev.get("ProcessID"), "process": ev.get("ProcessName")}
    return {k: str(v) for k, v in out.items() if v not in (None, "")}


def fmt_ts(ns):
    """epoch ns -> '2026-10-17T03:00:00.1234567+00:00' (SilkETW's 7 fractional digits)."""
    s = datetime.fromtimestamp(ns // 1_000_000_000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{s}.{(ns % 1_000_000_000) // 100:07d}+00:00"


def load(run_dir):
    """-> {provider: [(ts_ns, line)]} in file order (lines without a TimeStamp are skipped, like Alloy's
    timestamp stage would leave them unusable for the converter anyway)."""
    streams = {}
    for p in sorted(glob.glob(os.path.join(run_dir, "*.json"))):
        rows = []
        with open(p, encoding="utf-8-sig", errors="replace") as fh:
            for raw in fh:
                line = raw.rstrip("\r\n")
                m = _TS.search(line)
                if m:
                    try:
                        rows.append((ts_ns(m.group(2)), line))
                    except ValueError:
                        pass
        streams[os.path.splitext(os.path.basename(p))[0]] = rows
    return streams


def push_run(lk, run_dir, split, run, host="replay", shift_to_now=False, speed=0.0, log=print):
    streams = load(run_dir)
    if not any(streams.values()):
        sys.exit(f"no SilkETW lines in {run_dir}")
    dmap_file = os.path.join(run_dir, "dosmap.meta")
    dmap = json.load(open(dmap_file)) if os.path.exists(dmap_file) else infer_dos_map(
        [p for p in glob.glob(os.path.join(run_dir, "*.json"))])
    t_first = min(r[0][0] for r in streams.values() if r)
    t_last = max(r[-1][0] for r in streams.values() if r)
    shift = (time.time_ns() - t_last - 1_000_000_000) if shift_to_now else 0
    shift -= shift % 100                           # SilkETW resolution is 100 ns: keep line and Loki ts equal
    labels = lambda p: {"job": "silketw", "host": host, "split": split, "run": run, "provider": p}  # noqa: E731
    lk.push([{"stream": {"job": "etw-meta", "host": host, "split": split, "run": run},
              "values": [[str(t_first + shift), json.dumps(dmap)]]}])

    def entry(ts, line):
        if shift:
            ts += shift
            line = _TS.sub(lambda m: m.group(1) + fmt_ts(ts) + m.group(3), line, count=1)
        return [str(ts), line, meta(line)]

    total = sum(len(r) for r in streams.values())
    log(f"[loki_push] {run_dir} -> {lk.url}  {split}/{run}  {total:,} lines, "
        f"{(t_last - t_first) / 6e10:.1f} min{' shifted to end now' if shift else ''}"
        f"{f' at {speed}x' if speed else ''}  dos_map={dmap}")
    if not speed:
        for p, rows in streams.items():
            vals, size = [], 0
            for r in rows:
                e = entry(*r)
                vals.append(e)
                size += len(e[1]) + 100
                if size >= BATCH_BYTES:
                    lk.push([{"stream": labels(p), "values": vals}])
                    vals, size = [], 0
            if vals:
                lk.push([{"stream": labels(p), "values": vals}])
        log(f"[loki_push] pushed {total:,} lines")
        flush(lk, log)
        return total
    # paced: every wall second, push what a live tail would have produced by the virtual clock
    idx = {p: 0 for p in streams}
    v, wall0, sent = t_first, time.time(), 0
    while any(idx[p] < len(r) for p, r in streams.items()):
        v += int(speed * 1_000_000_000)
        batch = []
        for p, rows in streams.items():
            j = idx[p]
            while j < len(rows) and rows[j][0] < v:
                j += 1
            if j > idx[p]:
                batch.append({"stream": labels(p), "values": [entry(*r) for r in rows[idx[p]:j]]})
                sent += j - idx[p]
                idx[p] = j
        if batch:
            lk.push(batch)
        time.sleep(max(0.0, wall0 + (v - t_first) / 1e9 / speed - time.time()))
    log(f"[loki_push] pushed {sent:,} lines in {time.time() - wall0:.0f} s wall")
    flush(lk, log)
    return sent


def flush(lk, log):
    """POST /flush: Loki returns OLD-timestamped lines only once their in-memory chunk is flushed (observed on
    3.7.8; see roles/ubuntu_loki/templates/loki.yaml.j2). Best effort: the endpoint may be firewalled."""
    import urllib.request
    try:
        urllib.request.urlopen(urllib.request.Request(lk.url + "/flush", data=b"", method="POST"), timeout=30)
        log("[loki_push] asked Loki to flush (old-timestamp lines become queryable in a few seconds)")
    except OSError as e:
        log(f"[loki_push] /flush not possible ({e}); old-timestamp lines show up after chunk_idle_period")


def selftest():
    import tempfile
    from lokiclient import FakeLoki
    fake = FakeLoki()
    try:
        lk = Loki(fake.url)
        with tempfile.TemporaryDirectory() as d:
            lines = [json.dumps({"TimeStamp": f"2026-10-17T03:00:0{i}.1234567+07:00", "EventName": "Read",
                                 "ProcessID": 7, "ProcessName": "notepad",
                                 "XmlEventData": {"FileName": r"\Device\HarddiskVolume4\Windows\x"}}) for i in range(3)]
            with open(os.path.join(d, "kfile.json"), "w", newline="\r\n") as fh:   # SilkETW writes CRLF
                fh.write("\n".join(lines) + "\n")
            assert push_run(lk, d, "train", "r1", log=lambda *_: None) == 3
            got = sorted(e for e in fake.entries if e[1]["job"] == "silketw")
            assert [e[2] for e in got] == lines                            # line unchanged, CR stripped
            assert got[0][0] == ts_ns("2026-10-17T03:00:00.1234567+07:00")
            assert got[0][1] == {"job": "silketw", "host": "replay", "split": "train", "run": "r1",
                                 "provider": "kfile", "event": "Read", "pid": "7", "process": "notepad"}
            meta_e = [e for e in fake.entries if e[1]["job"] == "etw-meta"]
            assert json.loads(meta_e[0][2]) == {"\\device\\harddiskvolume4": "c:"}
            fake.entries.clear()
            push_run(lk, d, "test", "r2", shift_to_now=True, speed=100, log=lambda *_: None)
            got = sorted(e for e in fake.entries if e[1]["job"] == "silketw")
            assert abs(got[-1][0] - time.time_ns()) < 5e9                   # last event ~ now
            assert all(ts_ns(json.loads(e[2])["TimeStamp"]) == e[0] for e in got)   # line agrees with Loki ts
    finally:
        fake.close()
    print("[SELFTEST OK] Alloy-shaped labels + metadata, lines unchanged, dos map line, shift-to-now + pacing")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="recorded run folder (*.json, optional dosmap.meta)")
    ap.add_argument("--loki", default=os.environ.get("LOKI_URL", "http://127.0.0.1:3100"))
    ap.add_argument("--split", choices=["train", "test"], default="train")
    ap.add_argument("--run", help="run label (default: folder name)")
    ap.add_argument("--host", default="replay", help="host label (Alloy uses the VM's inventory name)")
    ap.add_argument("--shift-to-now", action="store_true", help="re-time so the run ends now (rewrites TimeStamp)")
    ap.add_argument("--speed", type=float, default=0, help="pace at N x real time (0 = bulk, as fast as possible)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.dir:
        ap.error("--dir is required")
    push_run(Loki(a.loki), a.dir, a.split, a.run or os.path.basename(os.path.normpath(a.dir)), a.host,
             a.shift_to_now, a.speed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
