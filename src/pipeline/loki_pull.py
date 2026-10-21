#!/usr/bin/env python3
"""loki_pull.py - pull raw SilkETW lines from Loki into an experiment folder (training input).

    Loki {job="silketw", split, run, provider}  ->  <out-root>/<id>/raw/<split>/<run>/<provider>.json
                                                     <out-root>/<id>/raw/<split>/<run>/dosmap.meta
                                                     <out-root>/<id>/labels/ledger.csv   (benign workload ledger)
                                                     <out-root>/<id>/pull.json            (what was pulled, sha256s)

Same raw layout 07-build-train-dataset.ps1 produced on the VM, so build_experiment.py (convert -> window ->
check -> experiment.json) and everything after it are unchanged. Lines are written exactly as Loki returns
them (Alloy never rewrites a line), oldest first, one file per provider.

Pick what to pull:
    uv run python src/pipeline/loki_pull.py                          interactive (arrow keys): runs or a custom range
    uv run python src/pipeline/loki_pull.py --list                   print the runs Loki has, then exit
    uv run python src/pipeline/loki_pull.py --latest --yes           newest train run, no prompts
    uv run python src/pipeline/loki_pull.py --run x-20261017T030000Z --yes --build
    uv run python src/pipeline/loki_pull.py --since 2h --split test --yes
    uv run python src/pipeline/loki_pull.py --start "2026-10-17 10:00+07:00" --end "2026-10-17 10:30+07:00" --yes

Times: ISO 8601 (offset optional; naive = UTC), "now", "now-2h" or "90m". --loki defaults to $LOKI_URL, else
http://127.0.0.1:3100 (Loki runs on the trainer with host networking).

    uv run python src/pipeline/loki_pull.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from lokiclient import Loki, iso, parse_time, selector  # noqa: E402

DEFAULT_OUT = os.path.join(SRC, "out", "etw", "experiments")
NS = 1_000_000_000


def tzinfo(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - tzdata missing: show UTC only
        return timezone.utc


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- discovery
def discover(lk, lookback_ns, split=None, now_ns=None, job="silketw"):
    """-> runs newest first: [{run, split, host, first_ns, last_ns, counts{provider: n}}].
    Built from label values + query_range (see Loki.label_values for why not /series)."""
    now_ns = now_ns or time.time_ns()
    start = now_ns - lookback_ns
    out = []
    pairs = [(sp, run) for sp in ([split] if split else lk.label_values("split", start, now_ns, query=selector(job=job)))
             for run in lk.label_values("run", start, now_ns, query=selector(job=job, split=sp))]
    for sp, run in pairs:                         # (split, run): the same run name may exist in both splits
        sel = selector(job=job, split=sp, run=run)
        f = lk.query_range(sel, start, now_ns + 1, limit=1, direction="forward")
        if not f:
            continue
        b = lk.query_range(sel, start, now_ns + 1, limit=1, direction="backward")
        lab = f[0][1]
        counts = lk.count_by(sel, "provider", f[0][0] - 1, b[0][0])
        out.append({"run": run, "split": lab["split"], "host": lab["host"], "first_ns": f[0][0],
                    "last_ns": b[0][0], "counts": counts, "providers": sorted(counts)})
    return sorted(out, key=lambda r: r["first_ns"], reverse=True)


def describe(r, tz):
    a, b = r["first_ns"], r["last_ns"]
    loc = f"{iso(a, tz)[:16]} -> {iso(b, tz)[11:16]} {iso(a, tz)[-5:]}"
    mins = (b - a) / NS / 60
    counts = " ".join(f"{p} {n:,}" for p, n in sorted(r["counts"].items()))
    return f"{r['run']:<26} {r['split']:<5} {loc}  ({mins:.0f} min, UTC {iso(a)[11:16]})  {counts}"


# --------------------------------------------------------------------------- selection
def pick_interactive(lk, a, runs, tz):
    """questionary prompts -> selection dict, or None if cancelled."""
    import questionary
    CUSTOM, CANCEL = "__custom__", "__cancel__"
    choices = [questionary.Choice(describe(r, tz), value=r) for r in runs]
    choices += [questionary.Choice("Custom time range ...", value=CUSTOM), questionary.Choice("Cancel", value=CANCEL)]
    default = next((c for c in choices if isinstance(c.value, dict) and c.value["split"] == (a.split or "train")), None)
    pick = questionary.select("What should be pulled from Loki?", choices=choices, default=default).ask()
    if pick in (None, CANCEL):
        return None
    if pick != CUSTOM:
        return {"mode": "run", "run": pick["run"], "split": pick["split"],
                "start_ns": pick["first_ns"], "end_ns": pick["last_ns"] + 1}
    start = questionary.text("Start (ISO 8601, 'now-2h', '90m'):", default="now-1h",
                             validate=lambda s: _valid_time(s)).ask()
    end = questionary.text("End:", default="now", validate=lambda s: _valid_time(s)).ask()
    if start is None or end is None:
        return None
    split = questionary.select("Split:", choices=["any", "train", "test"], default=a.split or "any").ask()
    provs = questionary.checkbox("Providers:", choices=[questionary.Choice(p, checked=True)
                                                        for p in ("kproc", "kfile", "knet")]).ask()
    if split is None or not provs:
        return None
    return {"mode": "range", "split": None if split == "any" else split, "providers": provs,
            "start_ns": parse_time(start), "end_ns": parse_time(end)}


def _valid_time(s):
    try:
        parse_time(s)
        return True
    except (ValueError, KeyError):
        return "not a time: use ISO 8601, 'now', 'now-2h' or '90m'"


def pick_flags(a, runs):
    """Selection from flags, or None when no selection flag was given."""
    if a.run:
        hits = [r for r in runs if r["run"] == a.run and (not a.split or r["split"] == a.split)]
        if len(hits) > 1:
            sys.exit(f"run {a.run!r} exists in splits {[r['split'] for r in hits]}: add --split")
        r = hits[0] if hits else None
        if r is None:
            sys.exit(f"run {a.run!r} not found in the last {a.lookback} (try --list or a longer --lookback)")
        return {"mode": "run", "run": r["run"], "split": r["split"], "start_ns": r["first_ns"],
                "end_ns": r["last_ns"] + 1}
    if a.latest:
        cand = [r for r in runs if r["split"] == (a.split or "train")]
        if not cand:
            sys.exit(f"no {a.split or 'train'} run in the last {a.lookback}")
        r = cand[0]
        return {"mode": "run", "run": r["run"], "split": r["split"], "start_ns": r["first_ns"],
                "end_ns": r["last_ns"] + 1}
    if a.since or a.start:
        start = parse_time(a.since if a.since else a.start)
        end = parse_time(a.end or "now")
        if end <= start:
            sys.exit("--end must be after --start")
        return {"mode": "range", "split": a.split, "providers": a.provider or ["kproc", "kfile", "knet"],
                "start_ns": start, "end_ns": end}
    return None


# --------------------------------------------------------------------------- pull
def pull(lk, sel, out_dir, page=5000, log=print, job="silketw"):
    """Write the selection into out_dir (must not exist). -> pull record (also saved as pull.json)."""
    tmp = out_dir + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    if sel["mode"] == "run":
        queries = [selector(job=job, split=sel["split"], run=sel["run"])]
    else:
        queries = [selector(job=job, split=sel["split"], provider=p) for p in sel["providers"]]
    handles, counts, dups = {}, {}, {}
    try:
        for q in queries:
            n, last_ts, at_ts = 0, None, set()
            for ts, lab, line in lk.query_all(q, sel["start_ns"], sel["end_ns"], page=page):
                key = (lab["split"], lab["run"], lab["provider"])
                # exact (ts, stream, line) repeats = the same event shipped twice (Alloy lost its positions, a
                # re-push): drop and count them. Entries arrive in ts order, so only the current ts is kept.
                if ts != last_ts:
                    last_ts, at_ts = ts, set()
                if (key, line) in at_ts:
                    dups[key] = dups.get(key, 0) + 1
                    continue
                at_ts.add((key, line))
                fh = handles.get(key)
                if fh is None:
                    d = os.path.join(tmp, "raw", *key[:2])
                    os.makedirs(d, exist_ok=True)
                    fh = handles[key] = open(os.path.join(d, key[2] + ".json"), "w", encoding="utf-8", newline="\n")
                fh.write(line.rstrip("\r\n") + "\n")
                counts[key] = counts.get(key, 0) + 1
                n += 1
                if n % 50000 == 0:
                    log(f"[loki_pull] {q}: {n:,} lines ...")
    finally:
        for fh in handles.values():
            fh.close()
    if not counts:
        shutil.rmtree(tmp)
        sys.exit("[loki_pull] nothing in that range/selection")

    # dos map + ledger per run. Both are Windows (SilkETW) concepts: the dos map translates
    # \Device\HarddiskVolumeN -> C:, and the workload ledger rides job="etw-meta"/"workload-ledger"
    # alongside SilkETW.
    meta, ledger_lines = {}, 0
    if job == "silketw":
        runs = sorted({k[:2] for k in counts})
        look0 = sel["start_ns"] - 7 * 86400 * NS
        for sp, run in runs:
            rows = lk.query_range(selector(job="etw-meta", run=run), look0, sel["end_ns"] + 86400 * NS,
                                  limit=1, direction="backward")
            path = os.path.join(tmp, "raw", sp, run, "dosmap.meta")
            if rows:
                dmap, src = json.loads(rows[0][2]), "loki etw-meta line (etwctl start on the VM, or loki_push.py)"
            else:
                sys.path.insert(0, os.path.join(SRC, "live"))
                from etwship import infer_dos_map
                files = [os.path.join(tmp, "raw", sp, run, p + ".json") for p in ("kproc", "kfile")
                         if os.path.exists(os.path.join(tmp, "raw", sp, run, p + ".json"))]
                dmap, src = infer_dos_map(files), "inferred from raw paths (no etw-meta line in Loki)"
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(dmap, fh)
            meta[run] = {"dos_map": dmap, "dos_map_source": src}

        for sp, run in runs:
            if not run.startswith("x-"):              # only 07's train runs carry a workload ledger
                continue
            rows = list(lk.query_all(selector(job="workload-ledger", exp=run[2:]), look0,
                                     sel["end_ns"] + 86400 * NS, page=page))
            if rows:
                os.makedirs(os.path.join(tmp, "labels"), exist_ok=True)
                head = [l for _, _, l in rows if l.startswith('"utc"')][:1]   # header carries no time: read time
                body = [l for _, _, l in rows if not l.startswith('"utc"')]
                with open(os.path.join(tmp, "labels", "ledger.csv"), "w", encoding="utf-8", newline="\n") as fh:
                    fh.writelines(l.rstrip("\r\n") + "\n" for l in head + body)
                ledger_lines += len(body)

    rec = {"source": "loki", "loki": lk.url, "pulled_utc": datetime.now(timezone.utc).isoformat(),
           "selection": {k: v for k, v in sel.items() if k not in ("start_ns", "end_ns")},
           "range_ns": [sel["start_ns"], sel["end_ns"]],
           "range_utc": [iso(sel["start_ns"]), iso(sel["end_ns"])], "queries": queries,
           "streams": [{"split": k[0], "run": k[1], "provider": k[2], "lines": n, "duplicates_dropped": dups.get(k, 0),
                        "sha256": sha256_file(os.path.join(tmp, "raw", *k[:2], k[2] + ".json"))}
                       for k, n in sorted(counts.items())],
           "runs": meta, "ledger_lines": ledger_lines,
           "tools": {"loki_pull": sha256_file(os.path.abspath(__file__)),
                     "lokiclient": sha256_file(os.path.join(HERE, "lokiclient.py"))}}
    with open(os.path.join(tmp, "pull.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    os.replace(tmp, out_dir)                       # all or nothing: a crash never leaves a half pull
    return rec


def default_id(sel):
    if sel["mode"] == "run":
        return sel["run"]
    return "range-" + datetime.fromtimestamp(sel["start_ns"] / NS, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------- selftest
def selftest():
    """Fake Loki with one train run (2 providers, dos map, ledger) and one test run: --run, --latest and a
    custom range pull the right lines, byte-exact, with pull.json sha256s. 'It runs' only."""
    import tempfile
    from lokiclient import FakeLoki
    fake = FakeLoki()
    try:
        lk = Loki(fake.url)
        t0 = 1_791_532_594 * NS

        def lab(split, run, prov, job="silketw"):
            return {"job": job, "host": "ws-etw", "split": split, "run": run, "provider": prov}
        tr = {"kproc": [f'{{"TimeStamp":"t{i}","EventName":"ProcessStart"}}' for i in range(7)],
              "kfile": [f'{{"TimeStamp":"t{i}","EventName":"Read"}}' for i in range(5)]}
        streams = [{"stream": lab("train", "x-20261017T030000Z", p),
                    "values": [[str(t0 + i * NS), l, {"event": "E"}] for i, l in enumerate(lines)]}
                   for p, lines in tr.items()]
        streams.append({"stream": lab("test", "x-20261017T030000Z", "kproc"),       # same name, other split
                        "values": [[str(t0 + 1800 * NS), '{"same-name":1}']]})
        streams.append({"stream": lab("test", "live-1", "kproc"),
                        "values": [[str(t0 + 3600 * NS + i), f'{{"n":{i}}}'] for i in range(3)]})
        streams.append({"stream": {"job": "etw-meta", "host": "ws-etw", "split": "train", "run": "x-20261017T030000Z"},
                        "values": [[str(t0), json.dumps({"\\device\\harddiskvolume4": "c:"})]]})
        streams.append({"stream": {"job": "workload-ledger", "host": "ws-etw", "exp": "20261017T030000Z"},
                        "values": [[str(t0 + 2 * NS), '"2026-10-17T03:00:02Z","browse","open_url","u","explorer-com"'],
                                   [str(t0 + 99 * NS), '"utc","scenario","action","target","launch_mode"']]})
        lk.push(streams)
        lk.push([{"stream": streams[0]["stream"], "values": streams[0]["values"][:2]}])   # re-sent: duplicates
        now = t0 + 7200 * NS
        runs = discover(lk, 86400 * NS, now_ns=now)
        assert [(r["split"], r["run"]) for r in runs] == [("test", "live-1"), ("test", "x-20261017T030000Z"),
                                                           ("train", "x-20261017T030000Z")], runs
        assert runs[2]["counts"] == {"kproc": 9, "kfile": 5}, runs[2]["counts"]   # Loki counts the re-sent lines
        assert runs[1]["counts"] == {"kproc": 1}                                   # not merged with train
        assert "x-20261017T030000Z" in describe(runs[1], tzinfo("Asia/Ho_Chi_Minh"))
        a = argparse.Namespace(run=None, latest=True, split=None, since=None, start=None, end=None,
                               provider=None, lookback="1d")
        sel = pick_flags(a, runs)
        assert sel["run"] == "x-20261017T030000Z"
        with tempfile.TemporaryDirectory() as d:
            rec = pull(lk, sel, os.path.join(d, "e1"), page=4, log=lambda *_: None)
            base = os.path.join(d, "e1", "raw", "train", "x-20261017T030000Z")
            for p, lines in tr.items():
                with open(os.path.join(base, p + ".json"), encoding="utf-8") as fh:
                    assert fh.read() == "".join(l + "\n" for l in lines), p
            assert json.load(open(os.path.join(base, "dosmap.meta"))) == {"\\device\\harddiskvolume4": "c:"}
            led = open(os.path.join(d, "e1", "labels", "ledger.csv")).read().splitlines()
            assert led[0].startswith('"utc"') and len(led) == 2 and rec["ledger_lines"] == 1
            assert {s["provider"]: s["lines"] for s in rec["streams"]} == {"kproc": 7, "kfile": 5}
            assert {s["provider"]: s["duplicates_dropped"] for s in rec["streams"]} == {"kproc": 2, "kfile": 0}
            assert rec["streams"][0]["sha256"] == sha256_file(os.path.join(base, rec["streams"][0]["provider"] + ".json"))
            # custom range: only kproc, seconds 2..4 of the train run
            a2 = argparse.Namespace(run=None, latest=False, split="train", since=None, provider=["kproc"],
                                    start=str(t0 + 2 * NS), end=str(t0 + 5 * NS), lookback="1d")
            rec2 = pull(lk, pick_flags(a2, runs), os.path.join(d, "e2"), log=lambda *_: None)
            assert [(s["provider"], s["lines"]) for s in rec2["streams"]] == [("kproc", 3)], rec2["streams"]
            assert pick_flags(argparse.Namespace(run="live-1", latest=False, split=None, since=None, start=None,
                                                 end=None, provider=None, lookback="1d"), runs)["split"] == "test"
    finally:
        fake.close()
    print("[SELFTEST OK] discovery + counts, --latest, custom range, byte-exact files, dos map, ledger, pull.json")


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loki", default=os.environ.get("LOKI_URL", "http://127.0.0.1:3100"))
    ap.add_argument("--lookback", default="7d", help="how far back to look for runs (e.g. 7d, 48h)")
    ap.add_argument("--tz", default=os.environ.get("LAB_TZ", "Asia/Ho_Chi_Minh"), help="zone for displayed times")
    g = ap.add_argument_group("selection (any of these skips the interactive picker)")
    g.add_argument("--run", help="one capture run, e.g. x-20261017T030000Z (07) or live-<UTC>")
    g.add_argument("--latest", action="store_true", help="the newest run of --split (default train)")
    g.add_argument("--start", help="custom range start")
    g.add_argument("--end", help="custom range end (default now)")
    g.add_argument("--since", help="custom range = [now - SINCE, now), e.g. 2h")
    ap.add_argument("--split", choices=["train", "test"], help="filter runs / the custom range")
    ap.add_argument("--provider", nargs="+", choices=["kproc", "kfile", "knet"], help="custom range only")
    ap.add_argument("--out-root", default=DEFAULT_OUT)
    ap.add_argument("--id", help="experiment folder name (default: run name, or range-<UTC start>)")
    ap.add_argument("--page", type=int, default=5000, help="lines per Loki request (server max_entries_limit)")
    ap.add_argument("--force", action="store_true", help="replace an existing experiment folder")
    ap.add_argument("--yes", action="store_true", help="no confirmation prompt")
    ap.add_argument("--build", action="store_true", help="then run build_experiment.py on the result")
    ap.add_argument("--list", action="store_true", help="list runs and exit")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()

    lk, tz = Loki(a.loki), tzinfo(a.tz)
    try:
        lk.ready()
    except OSError as e:
        sys.exit(f"[loki_pull] Loki not reachable at {a.loki} ({e}); set --loki or LOKI_URL")
    runs = discover(lk, parse_time("now") - parse_time(a.lookback), split=None)
    if a.list:
        for r in runs:
            print(describe(r, tz))
        return 0 if runs else 1

    sel = pick_flags(a, runs)
    interactive = sel is None
    if interactive:
        if not sys.stdin.isatty():
            sys.exit("[loki_pull] no selection flag and no terminal: use --run, --latest, --since or --start")
        sel = pick_interactive(lk, a, runs, tz)
        if sel is None:
            print("[loki_pull] cancelled")
            return 1
    if time.time_ns() - sel["end_ns"] < 180 * NS and sel["mode"] == "run":
        print("[loki_pull] WARNING: the run ended < 3 min ago; Alloy may still be shipping its last lines")
    exp_id = a.id or default_id(sel)
    if interactive and not a.id:
        import questionary
        exp_id = questionary.text("Experiment folder name:", default=exp_id).ask() or exp_id
    out_dir = os.path.join(a.out_root, exp_id)
    print(f"[loki_pull] {sel['mode']} {sel.get('run') or ''}  {iso(sel['start_ns'], tz)} -> {iso(sel['end_ns'], tz)}"
          f"  ->  {out_dir}")
    if os.path.exists(out_dir):
        if not a.force:
            sys.exit(f"[loki_pull] {out_dir} exists (use --force or --id)")
    if not a.yes:
        import questionary
        if not questionary.confirm("Pull now?", default=True).ask():
            return 1
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(a.out_root, exist_ok=True)
    rec = pull(lk, sel, out_dir, page=a.page)
    for s in rec["streams"]:
        print(f"[loki_pull]   {s['split']}/{s['run']}/{s['provider']}.json  {s['lines']:,} lines  sha256 {s['sha256'][:12]}"
              + (f"  ({s['duplicates_dropped']:,} duplicates dropped)" if s["duplicates_dropped"] else ""))
    for run, m in rec["runs"].items():
        print(f"[loki_pull]   {run}: dos map {m['dos_map']} ({m['dos_map_source']})")
    build = a.build
    if interactive and not build:
        import questionary
        build = bool(questionary.confirm("Build the experiment now (convert, window, check)?", default=True).ask())
    if build:
        import build_experiment
        splits = sorted({s["split"] for s in rec["streams"]})
        if len(splits) != 1:
            sys.exit(f"[loki_pull] pulled several splits {splits}; run build_experiment.py --split ... yourself")
        return build_experiment.main(["--exp", out_dir, "--split", splits[0]])
    nextcmd = f"uv run python src/pipeline/build_experiment.py --exp {out_dir}"
    print(f"[loki_pull] next: {nextcmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
