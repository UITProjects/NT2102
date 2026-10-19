#!/usr/bin/env python3
"""build_experiment.py - turn a pulled experiment folder into training input (host port of 07 steps 4-7).

    <exp>/raw/<split>/<run>/*.json + dosmap.meta     (loki_pull.py)
      4. convert   etw2nodlink.py --dos-map            -> <exp>/converted/   (un-windowed, + _summary.json)
      5. window    etwwindow.py (private staging dir)  -> <exp>/<graph>_<split>_{edges,node_attrs}.csv,
                                                          windows.csv, manifest.json
      6. check     check_rdf_input.py                  -> <exp>/check.json
      7. record    <exp>/experiment.json               window params, totals, tool sha256s (raw bytes, the
                                                       same rule engine.load_converter uses), pull.json

Each stage is a separate process writing standalone files, exactly as 07 ran them on the VM; the tools are
the same files Ansible deploys there (src/pipeline/), so a capture converted here and on the VM must agree.

    uv run python src/pipeline/build_experiment.py --exp src/out/etw/experiments/x-20261017T030000Z
    uv run python src/pipeline/build_experiment.py --selftest
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONVERTER = os.path.join(HERE, "etw2nodlink.py")            # Windows SilkETW capture
SYSMON_CONVERTER = os.path.join(HERE, "sysmon2nodlink.py")  # Linux Sysmon-for-Linux capture (--source sysmon)
WINDOWER = os.path.join(HERE, "etwwindow.py")
CHECKER = os.path.join(HERE, "check_rdf_input.py")


def sha(path):
    """sha256 of the raw file bytes (== Get-FileHash on the VM for the same bytes)."""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(args, ok=(0,)):
    print("[build] $", " ".join(os.path.basename(x) if x.endswith(".py") else x for x in args), flush=True)
    rc = subprocess.call([sys.executable, *args])
    if rc not in ok:
        sys.exit(f"[build] {os.path.basename(args[0])} exited with {rc}")
    return rc


def dos_map_file(exp, split, work):
    """All runs in the split must share one map (one VM). -> path of a JSON file for --dos-map, or None."""
    maps = {}
    for p in sorted(glob.glob(os.path.join(exp, "raw", split, "*", "dosmap.meta"))):
        with open(p, encoding="utf-8-sig") as fh:
            maps[os.path.basename(os.path.dirname(p))] = json.load(fh)
    if not maps:
        return None, {}
    distinct = {json.dumps(m, sort_keys=True) for m in maps.values()}
    if len(distinct) > 1:
        sys.exit(f"[build] runs disagree on the dos map: {maps}")
    path = os.path.join(work, "dosmap.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(distinct.pop())
    return path, next(iter(maps.values()))


def build(exp, split="train", graph="SimulatedW10", window_min=5, stride_min=2, max_gap_s=120, source="etw"):
    exp = os.path.abspath(exp)
    # Windows ships *.json (SilkETW); Linux ships *.log (Sysmon-for-Linux). Accept whichever is present.
    raw_glob = "*.json" if source == "etw" else "*"
    if not glob.glob(os.path.join(exp, "raw", split, "*", raw_glob)):
        sys.exit(f"[build] no raw/{split}/<run>/ capture files under {exp}")
    converter = SYSMON_CONVERTER if source == "sysmon" else CONVERTER
    rec_path = os.path.join(exp, "experiment.json")
    rec = json.load(open(rec_path, encoding="utf-8-sig")) if os.path.exists(rec_path) else {}
    pull_path = os.path.join(exp, "pull.json")
    pull = json.load(open(pull_path, encoding="utf-8")) if os.path.exists(pull_path) else None

    with tempfile.TemporaryDirectory() as work:
        # The dos map is a Windows concept (\Device\HarddiskVolumeN -> C:); Linux paths are already POSIX.
        dmap_path, dmap = (dos_map_file(exp, split, work) if source == "etw" else (None, {}))
        if source == "etw" and dmap_path is None:
            print("[build] WARNING: no dosmap.meta: paths stay \\device\\harddiskvolumeN\\... (off-Windows map is empty)")
        conv = os.path.join(exp, "converted")
        run([converter, "--raw-root", os.path.join(exp, "raw"), "--split", split, "--graph", graph, "--out", conv,
             *(["--dos-map", dmap_path] if (source == "etw" and dmap_path) else [])])

        # etwwindow swaps its whole --out folder in atomically: give it a private folder, never <exp> itself
        stage = os.path.join(exp, "_windowing")
        rc = run([WINDOWER, "--indir", conv, "--graph", graph, "--split", split, "--window-min", str(window_min),
                  "--stride-min", str(stride_min), "--max-gap-s", str(max_gap_s), "--out", stage, "--force"],
                 ok=(0, 2))
        if rc == 2:
            sys.exit(f"[build] no full {window_min}-min window in the capture (see {conv})")
        for f in os.listdir(stage):
            if os.path.isfile(os.path.join(stage, f)):
                os.replace(os.path.join(stage, f), os.path.join(exp, f))
        shutil.rmtree(stage)

    rc = run([CHECKER, "--dir", exp, "--graph", graph, "--split", split, "--out", os.path.join(exp, "check.json")],
             ok=(0, 1))
    t = json.load(open(os.path.join(exp, "manifest.json"), encoding="utf-8"))["totals"]
    rec.setdefault("id", os.path.basename(exp))
    rec.update({
        "graph": graph, "split": split, "event_source": source,
        "source": "loki" if pull else rec.get("source", "local raw"),
        "label_source": rec.get("label_source", "benign by construction: scripted workload (07/06), no attack "
                                                "executed; includes background OS activity"
                                                if split == "train" else "unlabelled"),
        "dos_map": dmap,
        "windowing": {"window_min": window_min, "stride_min": stride_min, "max_gap_s": max_gap_s},
        "results": {k: t.get(k) for k in ("windows", "edges_in", "edges_out", "edges_in_no_window",
                                          "nodes_unique", "node_window_copies")} | {"contract_ok": rc == 0},
        "tools": {"converter": sha(converter), "etwwindow": sha(WINDOWER), "checker": sha(CHECKER),
                  "builder": sha(os.path.abspath(__file__))},
        "pull": pull,
        "postprocessed_utc": datetime.now(timezone.utc).isoformat(),
    })
    with open(rec_path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    if rc != 0:
        sys.exit(f"[build] contract check FAILED - see {exp}/check.json (files kept for inspection)")
    print(f"[build] done  {t.get('windows')} windows, {t.get('edges_out')} edges -> {exp}")
    print(f"[build] converter sha256 {rec['tools']['converter']} (live server checks this via --experiment)")
    return rec


def selftest():
    """Synthetic 12-minute capture with a dos map -> 5-min windows exist, paths mapped to c:, record written."""
    def ev(prov, name, pid, pname, ms, **data):
        ts = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
        return json.dumps({"ProviderName": prov, "EventName": name, "ProcessID": pid, "ProcessName": pname,
                           "TimeStamp": ts, "XmlEventData": data})
    kp, kf = "Microsoft-Windows-Kernel-Process", "Microsoft-Windows-Kernel-File"
    base = 1_791_532_594_000
    with tempfile.TemporaryDirectory() as d:
        r = os.path.join(d, "exp", "raw", "train", "x-1")
        os.makedirs(r)
        proc = [ev(kp, "ProcessStart/Start", 100, "explorer", base, ProcessID="200", ParentProcessID="100",
                   ImageName=r"\Device\HarddiskVolume4\Windows\System32\notepad.exe")]
        files = []
        for i in range(72):                                 # one file op every 10 s for 12 min
            files.append(ev(kf, "Create", 200, "notepad", base + 1000 + i * 10_000, FileObject=f"0x{i}",
                            FileName=rf"\Device\HarddiskVolume4\Users\u\doc{i % 3}.txt"))
            files.append(ev(kf, "Write", 200, "notepad", base + 1500 + i * 10_000, FileObject=f"0x{i}"))
        for fn, lines in (("kproc.json", proc), ("kfile.json", files)):
            with open(os.path.join(r, fn), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        with open(os.path.join(r, "dosmap.meta"), "w") as fh:
            json.dump({"\\device\\harddiskvolume4": "c:"}, fh)
        rec = build(os.path.join(d, "exp"))
        attrs = open(os.path.join(d, "exp", "SimulatedW10_train_node_attrs.csv"), encoding="utf-8").read()
        assert rec["results"]["windows"] >= 3 and rec["results"]["contract_ok"], rec["results"]
        assert "c:\\users\\u\\doc0.txt" in attrs and "harddiskvolume4" not in attrs
        assert rec["tools"]["converter"] == sha(CONVERTER) and rec["windowing"]["window_min"] == 5
    print("[SELFTEST OK] convert -> window -> check -> experiment.json, dos map applied off Windows")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp", help="experiment folder (with raw/<split>/<run>/)")
    ap.add_argument("--split", default="train", choices=["train", "test"])
    ap.add_argument("--graph", default="SimulatedW10")
    ap.add_argument("--source", default="etw", choices=["etw", "sysmon"],
                    help="etw = Windows SilkETW (default); sysmon = Linux Sysmon-for-Linux (use --graph SimulatedU22)")
    ap.add_argument("--window-min", type=int, default=5)
    ap.add_argument("--stride-min", type=int, default=2)
    ap.add_argument("--max-gap-s", type=int, default=120)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.exp:
        ap.error("--exp is required")
    build(a.exp, a.split, a.graph, a.window_min, a.stride_min, a.max_gap_s, a.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
