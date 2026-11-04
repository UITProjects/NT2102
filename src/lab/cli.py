"""lab - the improve loop: capture in Loki -> experiment -> model -> live server.

Three words, three things (each lab option names exactly one of them):

  capture      what the Windows VM recorded: one SilkETW run, stored in Loki under its run label
               (07-build-train-dataset.ps1 names them x-<UTC>, start-live.ps1 live-<UTC>)
  experiment   a capture pulled from Loki and built into training input
               src/out/etw/experiments/<capture>/  (raw, edges, 5-min windows, experiment.json)
  model        features + trained OCRGCN, one folder each      src/artifacts/runs/<model>/
               (the live server loads the CURRENT model: `lab promote <model>`)

Commands (`lab <command> -h` shows every option with examples):

  lab status                          what you have: current model, experiments, models
  lab loop                            capture -> experiment -> model, in one go (interactive)
  lab pull                            capture -> experiment only
  lab train  --experiment ID          experiment -> model only
  lab promote MODEL                   make MODEL the one the live server loads
  lab compare                         two models on the same replayed windows: red counts per label
  lab current                         print the current model's paths
  lab selftest                        prove the plumbing runs (fake Loki, tiny model)

Typical session on the trainer:
  source .venv/bin/activate           (once per shell; uv sync installed `lab` into .venv/bin)
  lab status
  lab loop                            pick the capture with the arrow keys, name the model
  lab promote <model> && sudo systemctl restart ocr-live
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "src")
PIPE = os.path.join(SRC, "pipeline")
EXPS = os.path.join(SRC, "out", "etw", "experiments")
RUNS = os.path.join(SRC, "artifacts", "runs")
POINTER = os.path.join(SRC, "artifacts", "current.json")
LOCAL = os.path.join(SRC, "artifacts", "current.local.json")
CONVERTER = os.path.join(PIPE, "etw2nodlink.py")
GRAPH = "SimulatedW10"
SPLITS = ("train", "test")


def edges_file(exp, split="train"):
    """<exp>/SimulatedW10_<split>_edges.csv, the pooled windowed edges build_experiment.py writes."""
    return os.path.join(exp, f"{GRAPH}_{split}_edges.csv")


NS = 1_000_000_000


def rel(p):
    """Repo-relative for paths inside the repo (portable pointer files), absolute otherwise."""
    p = os.path.abspath(p)
    return os.path.relpath(p, REPO) if p.startswith(REPO + os.sep) else p


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(args, ok=(0,)):
    """One pipeline stage as its own process (same interpreter = the project venv)."""
    print("[lab] $", " ".join(rel(x) if os.path.isabs(x) and x.startswith(REPO) else x for x in args), flush=True)
    rc = subprocess.call([sys.executable, *args], cwd=REPO)
    if rc not in ok:
        sys.exit(f"[lab] {os.path.basename(args[0])} exited with {rc}")
    return rc


# --------------------------------------------------------------------------- current model pointer
def current():
    """-> {"run", "run_dir", "scaler", "experiment", "pointer"} with absolute paths, or {} if no pointer."""
    path = LOCAL if os.path.exists(LOCAL) else POINTER
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        c = json.load(fh)
    out = {"run": c.get("run"), "pointer": path}
    for k in ("run_dir", "scaler", "experiment"):
        v = c.get(k)
        out[k] = os.path.normpath(os.path.join(REPO, v)) if v else v
    return out


def pointer_for(run_dir):
    """Pointer record for a run folder made by `lab train` (paths relative to the repo)."""
    return {"run": os.path.basename(run_dir), "run_dir": rel(run_dir),
            "scaler": rel(os.path.join(run_dir, "features", "feature_scaler.json")),
            "experiment": rel(os.path.join(run_dir, "experiment.json"))}


def converter_check(experiment_json):
    if not experiment_json or not os.path.exists(experiment_json):
        return "no experiment.json"
    want = json.load(open(experiment_json, encoding="utf-8-sig")).get("tools", {}).get("converter", "")
    return "match" if want == sha(CONVERTER) else f"MISMATCH (trained with {want[:12]}, now {sha(CONVERTER)[:12]})"


# --------------------------------------------------------------------------- status
def _edge_ts(path):
    """(first, last) TimeStamp string of a SilkETW file, reading only its two ends."""
    import re
    rx = re.compile(r'"TimeStamp"\s*:\s*"([^"]+)"')
    with open(path, "rb") as fh:
        first = rx.search(fh.readline().decode("utf-8", "replace"))
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 65536))
        tail = fh.read().decode("utf-8", "replace").splitlines()
    last = next((m for m in (rx.search(l) for l in reversed(tail)) if m), None)
    return (first.group(1) if first else None), (last.group(1) if last else None)


def coverage(exp_dir):
    """{provider: (minutes covered, first, last)} and the capture span in minutes."""
    sys.path.insert(0, PIPE)
    from lokiclient import ts_ns
    out = {}
    for f in sorted(glob.glob(os.path.join(exp_dir, "raw", "*", "*", "k*.json"))):
        a, b = _edge_ts(f)
        if a and b:
            out[os.path.splitext(os.path.basename(f))[0]] = (ts_ns(a), ts_ns(b))
    if not out:
        return {}, 0.0
    span = (max(b for _, b in out.values()) - min(a for a, _ in out.values())) / NS / 60
    return {p: ((b - a) / NS / 60, a, b) for p, (a, b) in out.items()}, span


def cmd_status(a):
    c = current()
    print("current model")
    if c:
        print(f"  {c['run']}  ({rel(c['pointer'])})")
        print(f"  run_dir {rel(c['run_dir'])}   scaler {rel(c['scaler'])}")
        print(f"  converter_check vs today's etw2nodlink.py: {converter_check(c.get('experiment'))}")
    else:
        print("  none (no src/artifacts/current.json)")
    print("\nexperiments  (src/out/etw/experiments; per provider: minutes with events / capture minutes)")
    for d in sorted(glob.glob(os.path.join(EXPS, "*")), key=os.path.getmtime, reverse=True)[:a.limit]:
        if not os.path.isdir(d):
            continue
        e = os.path.join(d, "experiment.json")
        rec = json.load(open(e, encoding="utf-8-sig")) if os.path.exists(e) else {}
        res = rec.get("results", {})
        cov, span = coverage(d)
        flags = [p for p, (m, _, _) in cov.items() if span and m < 0.5 * span]
        built = next((f"built:{sp}" for sp in SPLITS if os.path.exists(edges_file(d, sp))), "NOT built")
        print(f"  {os.path.basename(d):<26} {rec.get('source', 'vm-07'):<9} {built:<11} windows {res.get('windows', '-')!s:<3} "
              + " ".join(f"{p} {m:.0f}/{span:.0f}m" for p, (m, _, _) in cov.items())
              + (f"   WARNING short coverage: {', '.join(flags)}" if flags else ""))
    print("\nmodels  (src/artifacts/runs; * = current)")
    for d in sorted(glob.glob(os.path.join(RUNS, "*")), key=os.path.getmtime, reverse=True)[:a.limit]:
        lin = os.path.join(d, "lineage.json")
        src = json.load(open(lin)).get("experiment", "?") if os.path.exists(lin) else "(no lineage.json)"
        mark = "*" if c and os.path.abspath(d) == os.path.abspath(c.get("run_dir") or "") else " "
        print(f" {mark}{os.path.basename(d):<26} from {src}   converter_check {converter_check(os.path.join(d, 'experiment.json'))}")
    return 0


# --------------------------------------------------------------------------- pull / train / loop
def loki_pull_args(a):
    """lab's capture options -> loki_pull.py flags (that script calls a capture a "run")."""
    out = []
    if a.capture:
        out += ["--run", a.capture]
    if a.latest:
        out.append("--latest")
    for flag in ("since", "start", "end", "split", "lookback", "loki"):
        v = getattr(a, flag, None)
        if v:
            out += ["--" + flag, v]
    if a.yes:
        out.append("--yes")
    return out


def pull(loki_args, out_root=None):
    """loki_pull.py --build; -> the experiment folder it produced (the new or updated one)."""
    before = {d: os.path.getmtime(d) for d in glob.glob(os.path.join(EXPS, "*"))}
    args = [*loki_args, "--build", "--out-root", out_root or EXPS]
    run([os.path.join(PIPE, "loki_pull.py"), *args])
    after = [d for d in glob.glob(os.path.join(EXPS, "*"))
             if os.path.isdir(d) and (d not in before or os.path.getmtime(d) > before[d])]
    if not after:
        sys.exit("[lab] the pull produced no experiment folder")
    return max(after, key=os.path.getmtime)


def load_ranges(paths):
    """Range files -> [{"start_ms", "end_ms", "reason", "label", "file"}]. One file: {"ranges": [{"start": ISO 8601,
    "end": ISO 8601, "reason": str, "label": str (lab compare only)}]}."""
    sys.path.insert(0, PIPE)
    from lokiclient import ts_ns
    out = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for r in json.load(fh)["ranges"]:
                a, b = ts_ns(r["start"]) // 1_000_000, ts_ns(r["end"]) // 1_000_000
                if a >= b:
                    sys.exit(f"[lab] {path}: range {r['start']} - {r['end']} is empty")
                out.append({"start_ms": a, "end_ms": b, "reason": r.get("reason", ""), "label": r.get("label"),
                            "file": os.path.basename(path)})
    return out


def overlaps(start_ms, end_ms, r):
    """Half-open [start, end) windows: a window that only touches a range's edge does not overlap it."""
    return start_ms < r["end_ms"] and end_ms > r["start_ms"]


def filter_windows(exp, edges, ranges, out_path):
    """Copy the pooled edges without the windows that overlap a range. -> counts for lineage.json.

    A window is dropped whole, never trimmed: its node copies and features only mean something as one 5-min graph.
    Streamed line by line: the pooled file of a 2-day range does not need to fit in the trainer's RAM."""
    with open(os.path.join(exp, "windows.csv"), newline="", encoding="utf-8") as fh:
        windows = list(csv.DictReader(fh))
    drop = {w["window"] for w in windows
            if any(overlaps(int(w["start_ms"]), int(w["end_ms"]), r) for r in ranges)}
    kept = dropped = 0
    with open(edges, newline="", encoding="utf-8") as src, open(out_path, "w", newline="", encoding="utf-8") as dst:
        r, w = csv.reader(src), csv.writer(dst, lineterminator="\n")
        w.writerow(next(r))
        for row in r:
            if row[0].split("_", 1)[0] in drop:                 # node ids are "<window>_<id>"
                dropped += 1
            else:
                w.writerow(row)
                kept += 1
    if not kept:
        sys.exit(f"[lab] the exclusions drop every window of {rel(exp)}")
    print(f"[lab] excluded {len(drop)} of {len(windows)} windows; {kept:,} edges kept, {dropped:,} dropped", flush=True)
    return {"windows_total": len(windows), "windows_dropped": len(drop), "windows_kept": len(windows) - len(drop),
            "edges_kept": kept, "edges_dropped": dropped}


def train(experiment, model=None, seeds=3, epochs=None, extra=(), benign_split="train", exclude=()):
    """features + OCRGCN for one experiment into a NEW model folder src/artifacts/runs/<model>/.

    benign_split="test" trains on a test-split build (an idle range pulled from Loki, D2). That is an explicit
    opt-in: a test split may contain an attack, so the operator asserts it is benign and lineage.json says so.

    exclude = range files (load_ranges): windows overlapping them are left out of features and training, and the
    ranges are copied into the model folder. That is how a long range skips an attack or a human session."""
    exp = experiment if os.path.isdir(experiment) else os.path.join(EXPS, experiment)
    edges = edges_file(exp, benign_split)
    if not os.path.exists(edges):
        other = [sp for sp in SPLITS if sp != benign_split and os.path.exists(edges_file(exp, sp))]
        hint = (f"; this experiment is built as split {other[0]!r}: if that range is benign (an idle Loki pull),"
                f" add --benign-split {other[0]}") if other else ": build the experiment first (lab pull)"
        sys.exit(f"[lab] {rel(edges)} missing{hint}")
    rec = json.load(open(os.path.join(exp, "experiment.json"), encoding="utf-8-sig"))
    if rec.get("split", "train") != benign_split:
        sys.exit(f"[lab] experiment.json says split {rec.get('split')!r}, not {benign_split!r}: rebuild it")
    run_dir = os.path.join(RUNS, model or "model-" + os.path.basename(os.path.normpath(exp)))
    if os.path.exists(run_dir):
        sys.exit(f"[lab] model {os.path.basename(run_dir)} already exists ({rel(run_dir)}); "
                 "pick another --model (a trained model is never overwritten)")
    feats = os.path.join(run_dir, "features")
    ranges = load_ranges(exclude)                               # before makedirs: a bad file leaves no folder behind
    train_edges = os.path.join(run_dir, "train_edges.csv") if ranges else edges
    trainer = ["--runs", str(seeds), *(["--epochs", str(epochs)] if epochs else []), *extra]
    cmds = [[os.path.join(PIPE, "encode_to_features.py"), "--edges", train_edges, "--out-dir", feats],
            [os.path.join(PIPE, "train_gnn_models.py"), "--edges", train_edges,
             "--features", os.path.join(feats, "features_model.csv"),
             "--scaler", os.path.join(feats, "feature_scaler.json"), "--out-dir", run_dir, *trainer]]
    os.makedirs(run_dir)
    excluded = None
    try:
        if ranges:
            excluded = filter_windows(exp, edges, ranges, train_edges)
            excluded["ranges"] = ranges
            for f in exclude:
                shutil.copy2(f, run_dir)
        for c in cmds:
            run(c)
    except SystemExit:
        failed = run_dir + ".failed"
        shutil.rmtree(failed, ignore_errors=True)
        os.replace(run_dir, failed)
        print(f"[lab] training failed; partial output kept in {rel(failed)}")
        raise
    shutil.copy2(os.path.join(exp, "experiment.json"), os.path.join(run_dir, "experiment.json"))
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "src/pipeline"], cwd=REPO,
                           capture_output=True, text=True).stdout.strip()
    lineage = {"model": os.path.basename(run_dir), "experiment": os.path.basename(os.path.normpath(exp)),
               "experiment_source": rec.get("source"), "benign_split": benign_split,
               "label_source": label_source(rec, benign_split, excluded), "excluded": excluded,
               "seeds": seeds, "epochs": epochs,
               "created_utc": datetime.now(timezone.utc).isoformat(),
               "git_commit": git.stdout.strip() or None, "pipeline_uncommitted_changes": bool(dirty),
               "converter_sha256": rec.get("tools", {}).get("converter"),
               "tools": {os.path.basename(p): sha(os.path.join(PIPE, p))
                         for p in ("encode_to_features.py", "train_gnn_models.py")},
               "commands": [" ".join(rel(x) if os.path.isabs(x) else x for x in c) for c in cmds]}
    with open(os.path.join(run_dir, "lineage.json"), "w") as fh:
        json.dump(lineage, fh, indent=1)
    print(f"[lab] model ready: {rel(run_dir)}")
    print(f"[lab] next: lab promote {os.path.basename(run_dir)}")
    return run_dir


def label_source(rec, benign_split, excluded=None):
    """What makes the training data benign, as recorded in lineage.json."""
    if benign_split == "train":
        return rec.get("label_source")
    rng = (rec.get("pull") or {}).get("range_utc") or ["?", "?"]
    if excluded:                                                # the range itself may hold an attack: only the rest is asserted
        return (f"benign by operator assertion: idle Loki range {rng[0]} - {rng[1]} (split {benign_split}) minus "
                f"{excluded['windows_dropped']} of {excluded['windows_total']} windows listed in `excluded`")
    return f"benign by operator assertion: idle Loki range {rng[0]} - {rng[1]} (split {benign_split}), no attack run"


def ask_model_name(exp):
    import questionary
    return questionary.text("Name for the new model:", default="model-" + os.path.basename(exp)).ask()


def promote(run, shared=False):
    run_dir = run if os.path.isdir(run) else os.path.join(RUNS, run)
    rec = pointer_for(run_dir)
    for k in ("scaler", "experiment"):
        if not os.path.exists(os.path.join(REPO, rec[k])):
            sys.exit(f"[lab] {rec[k]} missing: only models made by `lab train` / `lab loop` can be promoted")
    if not glob.glob(os.path.join(run_dir, "models", "*.pt")):
        sys.exit(f"[lab] no models in {rel(run_dir)}/models")
    dest = POINTER if shared else LOCAL
    with open(dest, "w") as fh:
        json.dump(rec, fh, indent=1)
    print(f"[lab] current model = {rec['run']}  ({rel(dest)})")
    print(f"[lab] converter_check: {converter_check(os.path.join(REPO, rec['experiment']))}")
    if shutil.which("systemctl") and os.path.exists("/etc/systemd/system/ocr-live.service"):
        print("[lab] restart the live server to load it:  sudo systemctl restart ocr-live")
    return rec


# --------------------------------------------------------------------------- compare
def scored_windows(sessions_dir):
    """{start_ms: row} of every scored window in <sessions_dir>/*/windows.csv (one replay output folder)."""
    out = {}
    for f in sorted(glob.glob(os.path.join(sessions_dir, "*", "windows.csv"))):
        with open(f, newline="", encoding="utf-8") as fh:
            for w in csv.DictReader(fh):
                if w["status"] == "scored":
                    out[int(w["start_ms"])] = w
    if not out:
        sys.exit(f"[lab] no scored windows under {sessions_dir}")
    return out


def compare(baseline, candidates, label_files=(), out_csv=None):
    """Same windows, two kinds of model: red windows per label, and every window that any model turned red.

    Only windows scored by ALL models count, so a replay that stopped early cannot look like fewer alerts.
    A label comes from the first range the window overlaps; other windows are "idle" (an operator assertion).
    Red = the `alert` column, so the cut-off is whatever the replay server ran with: use the same one for all."""
    import statistics
    base = scored_windows(baseline)
    cands = {os.path.basename(os.path.normpath(c)): scored_windows(c) for c in candidates}
    starts = sorted(set(base).intersection(*cands.values()))
    if not starts:
        sys.exit("[lab] the models share no scored window: replay the same runs through each")
    ranges = load_ranges(label_files)

    def label(w):
        return next((r["label"] or "labelled" for r in ranges if overlaps(int(w["start_ms"]), int(w["end_ms"]), r)), "idle")

    def red(w):
        return w["alert"] == "True"

    names = list(cands)
    bname = os.path.basename(os.path.normpath(baseline))
    rows = []
    for s in starts:
        b = base[s]
        rows.append({"start_utc": b["start_utc"], "label": label(b), "edges": b["edges"], f"{bname}_score": b["max_sg_score"],
                     f"{bname}_red": red(b),
                     **{k: v for n in names for k, v in ((f"{n}_score", cands[n][s]["max_sg_score"]),
                                                         (f"{n}_red", red(cands[n][s])))}})
    print(f"[lab] {len(rows)} windows scored by all models (baseline {len(base)}, "
          + ", ".join(f"{n} {len(cands[n])}" for n in names) + ")")
    print(f"{'label':<20} {'windows':>7}  {bname + ' red':>16}  candidates red (per model; mean ± std)")
    summary = {}
    for lab in sorted({r["label"] for r in rows}):
        g = [r for r in rows if r["label"] == lab]
        per = [sum(r[f"{n}_red"] for r in g) for n in names]
        spread = f"  {statistics.mean(per):.1f} ± {statistics.stdev(per):.1f}" if len(per) > 1 else ""
        b_red = sum(r[f"{bname}_red"] for r in g)
        summary[lab] = {"windows": len(g), "baseline_red": b_red, "candidates_red": dict(zip(names, per))}
        print(f"{lab:<20} {len(g):>7}  {b_red:>16}  {' '.join(map(str, per))}{spread}")
    print("\nwindows red in any model:")
    for r in rows:
        if r[f"{bname}_red"] or any(r[f"{n}_red"] for n in names):
            print(f"  {r['start_utc']} {r['label']:<20} {bname} {float(r[f'{bname}_score']):7.1f}  "
                  + "  ".join(f"{n} {float(r[f'{n}_score']):7.1f}" for n in names))
    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"[lab] wrote {out_csv}")
    return summary


# --------------------------------------------------------------------------- selftest
def selftest():
    """Fake Loki <- synthetic 16-min capture (Alloy-shaped) -> lab pull -> lab train (tiny) -> promote.
    Proves the plumbing and the run-folder layout. 'It runs' only, never 'it detects attacks'."""
    import random
    import tempfile
    global EXPS, RUNS, POINTER, LOCAL
    sys.path[:0] = [PIPE, os.path.join(SRC, "live")]
    from lokiclient import FakeLoki, Loki
    from loki_push import push_run
    saved = (EXPS, RUNS, POINTER, LOCAL)
    fake = FakeLoki()
    try:
        with tempfile.TemporaryDirectory() as d:
            EXPS, RUNS = os.path.join(d, "experiments"), os.path.join(d, "runs")
            POINTER, LOCAL = os.path.join(d, "current.json"), os.path.join(d, "current.local.json")
            os.makedirs(EXPS)
            cap = os.path.join(d, "cap", "x-20261017T030000Z")
            os.makedirs(cap)
            _synthetic_capture(cap, random.Random(7))
            push_run(Loki(fake.url), cap, "train", "x-20261017T030000Z", log=lambda *_: None)
            exp = pull(["--loki", fake.url, "--latest", "--yes", "--lookback", "3650d"])
            assert os.path.exists(edges_file(exp)), exp
            rd = train(os.path.basename(exp), "t1", seeds=1, epochs=3)
            for f in ("experiment.json", "lineage.json", "features/feature_scaler.json", "train_meta.json"):
                assert os.path.exists(os.path.join(rd, f)), f
            assert glob.glob(os.path.join(rd, "models", "*.pt"))
            try:
                train(os.path.basename(exp), "t1")
                raise AssertionError("a second train into the same run must be refused")
            except SystemExit:
                pass
            promote("t1")
            c = current()
            assert c["run"] == "t1" and c["pointer"] == LOCAL and c["run_dir"] == os.path.abspath(rd), c
            assert converter_check(c["experiment"]) == "match"
            # an idle range pulled as split test (D2): refused without the opt-in, trained with it
            push_run(Loki(fake.url), cap, "test", "live-20261017T030000Z", log=lambda *_: None)
            texp = pull(["--loki", fake.url, "--since", "3h", "--split", "test", "--id", "range-t", "--yes"])
            assert os.path.exists(edges_file(texp, "test")) and not os.path.exists(edges_file(texp)), texp
            try:
                train(texp, "t2", seeds=1, epochs=3)
                raise AssertionError("a test-split experiment must need --benign-split test")
            except SystemExit as e:
                assert "--benign-split test" in str(e), e
            rd2 = train(texp, "t2", seeds=1, epochs=3, benign_split="test")
            lin = json.load(open(os.path.join(rd2, "lineage.json")))
            assert lin["benign_split"] == "test" and "operator assertion" in lin["label_source"], lin
            # exclusions: a window overlapping a range leaves features and training; a touching one stays
            with open(os.path.join(texp, "windows.csv"), newline="") as fh:
                wins = list(csv.DictReader(fh))
            hit, nxt = wins[0], next(w for w in wins if int(w["start_ms"]) >= int(wins[0]["end_ms"]))
            rf = os.path.join(d, "ex.json")
            with open(rf, "w") as fh:
                json.dump({"ranges": [{"start": hit["start_utc"], "end": hit["end_utc"], "reason": "selftest"}]}, fh)
            gone = {w["window"] for w in wins if int(w["start_ms"]) < int(hit["end_ms"])}
            # 3 of the 6 synthetic windows remain: too few for the default purge of 2
            rd3 = train(texp, "t3", seeds=1, epochs=3, extra=["--purge-windows", "0"], benign_split="test", exclude=[rf])
            with open(os.path.join(rd3, "train_edges.csv"), newline="") as fh:
                seen = {row["source-id"].split("_", 1)[0] for row in csv.DictReader(fh)}
            assert not seen & gone and nxt["window"] in seen, (gone, sorted(seen)[:4])
            lin = json.load(open(os.path.join(rd3, "lineage.json")))
            assert lin["excluded"]["windows_dropped"] == len(gone) and "minus" in lin["label_source"], lin
            assert os.path.exists(os.path.join(rd3, "ex.json"))
            # compare: same windows only, red counted per label
            cols = ["window", "start_ms", "end_ms", "start_utc", "status", "edges", "max_sg_score", "alert"]
            for name, rows_ in (("a", [(0, "scored", "20.0", True), (1, "scored", "0.0", False), (2, "scored", "50.0", True)]),
                                ("b", [(0, "scored", "1.0", False), (1, "scored", "12.0", True), (3, "scored", "90.0", True)])):
                os.makedirs(os.path.join(d, name, "replay-x"))
                with open(os.path.join(d, name, "replay-x", "windows.csv"), "w", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(cols)
                    for i, st, sc, al in rows_:
                        w.writerow([f"w{i}", i * 300_000, (i + 1) * 300_000, f"t{i}", st, 1, sc, al])
            lf = os.path.join(d, "labels.json")
            with open(lf, "w") as fh:
                json.dump({"ranges": [{"start": "1970-01-01T00:05:00Z", "end": "1970-01-01T00:10:00Z", "label": "attack"}]}, fh)
            res = compare(os.path.join(d, "a"), [os.path.join(d, "b")], [lf], os.path.join(d, "cmp.csv"))
            assert res == {"attack": {"windows": 1, "baseline_red": 0, "candidates_red": {"b": 1}},
                           "idle": {"windows": 1, "baseline_red": 1, "candidates_red": {"b": 0}}}, res
    finally:
        fake.close()
        EXPS, RUNS, POINTER, LOCAL = saved
    print("[SELFTEST OK] Loki -> pull + build -> features + train into one run folder -> promote -> current;"
          " test-split idle range needs --benign-split test; exclusions drop whole windows; compare counts red per label")
    return 0


def _synthetic_capture(run_dir, rng):
    """16 minutes of SilkETW-like events: a few processes starting, loading DLLs, touching files, talking TCP."""
    def ev(prov, name, pid, pname, ms, **data):
        ts = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "0+00:00"
        return json.dumps({"ProviderName": "Microsoft-Windows-Kernel-" + prov, "EventName": name, "ProcessID": pid,
                           "ProcessName": pname, "TimeStamp": ts, "XmlEventData": data})
    base = int(datetime.now(timezone.utc).timestamp() * 1000) - 3600_000
    vol = r"\Device\HarddiskVolume4"
    kp, kf, kn = [], [], []
    procs = []
    for i in range(16 * 6):                                   # every 10 s
        t = base + i * 10_000
        if i % 4 == 0:                                        # a new process every 40 s
            pid, img = 1000 + i, rng.choice(["notepad", "msedge", "cmd", "svchost"])
            kp.append(ev("Process", "ProcessStart/Start", 500, "explorer", t, ProcessID=str(pid),
                         ParentProcessID="500", ImageName=rf"{vol}\Windows\System32\{img}.exe"))
            procs.append((pid, img))
        for pid, img in procs[-4:]:
            kp.append(ev("Process", "ImageLoad", pid, img, t + 100, ProcessID=str(pid),
                         ImageName=rf"{vol}\Windows\System32\dll{rng.randint(0, 9)}.dll"))
            fo = f"0x{rng.randint(0, 1 << 30):x}"
            kf.append(ev("File", "Create", pid, img, t + 200, FileObject=fo,
                         FileName=rf"{vol}\Users\u\doc{rng.randint(0, 19)}.txt"))
            kf.append(ev("File", rng.choice(["Read", "Write"]), pid, img, t + 300, FileObject=fo))
            kn.append(ev("Network", "KERNEL_NETWORK_TASK_TCPIP/Datasent.", 4, "System", t + 400, PID=str(pid),
                         daddr=f"10.0.0.{rng.randint(1, 5)}", dport="443", size="100"))
    for name, lines in (("kproc", kp), ("kfile", kf), ("knet", kn)):
        with open(os.path.join(run_dir, name + ".json"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- command line
EX_LOOP = """examples:
  lab loop                                    interactive: pick the capture, name the model
  lab loop --latest --model exp02 --yes       newest TRAIN capture, no questions
  lab loop --capture x-20261017T030000Z --model exp02 --yes
  lab loop --capture x-20261009T145632Z --split train --lookback 30d --model exp02 --seeds 1 --yes
                                              an older capture (> 7 days: --lookback), one seed (quick test)
  lab loop --since 2h --split train --model exp03 --yes      a time range instead of one capture

after it: lab promote <model> && sudo systemctl restart ocr-live"""

EX_PULL = """examples:
  lab pull                                    interactive picker (captures, or a custom time range)
  lab pull --list                             just list the captures Loki has
  lab pull --latest --yes                     newest TRAIN capture
  lab pull --capture x-20261017T030000Z --yes
  lab pull --start "2026-10-17 10:00+07:00" --end "2026-10-17 10:30+07:00" --split train --yes"""

EX_TRAIN = """examples:
  lab train --experiment x-20261017T030000Z --model exp02
  lab train --experiment x-20261017T030000Z --model exp02-quick --seeds 1 --epochs 30
  lab train --experiment x-20261017T030000Z --model exp02-h64 --trainer-args "--hidden 64 --lr 0.001"
  lab train --experiment range-20261102T114500Z --model exp06 --benign-split test
                                    an idle range pulled with --split test (D2); you assert it is benign
  lab train --experiment range-2d-20261104 --model exp07 --benign-split test \\
      --exclude-file src/artifacts/exclusions/proxmox-20261104.json
                                    a long range minus the windows overlapping the listed ranges
the experiment must be built first (lab pull); the model folder must not exist yet"""

EX_COMPARE = """examples:
  lab compare --baseline src/out/etw/eval/exp06 --candidate src/out/etw/eval/exp07-val-s360 \\
      --candidate src/out/etw/eval/exp07-val-s361 --label-file src/artifacts/exclusions/labels-20261104.json
each DIR is a live_server.py --sessions-dir that replayed the SAME runs at the SAME --sg-moderate-from.
Never compare a model on windows it trained on: fewer alerts there prove nothing."""

EX_PROMOTE = """examples:
  lab promote exp02                 this machine only (writes src/artifacts/current.local.json, gitignored)
  lab promote exp02 --shared        for everyone (writes src/artifacts/current.json; commit it + the model)
then restart the live server:  sudo systemctl restart ocr-live"""


def _parser(sub, name, summary, description, epilog=None):
    return sub.add_parser(name, help=summary, description=description, epilog=epilog,
                          formatter_class=argparse.RawDescriptionHelpFormatter)


def _capture_options(p):
    g = p.add_argument_group("which CAPTURE to pull from Loki (none of these = interactive picker)")
    g.add_argument("--capture", metavar="NAME", help="one capture by name, e.g. x-20261017T030000Z (see: lab pull --list)")
    g.add_argument("--latest", action="store_true", help="the newest capture of --split (default: train)")
    g.add_argument("--since", metavar="AGE", help="a time range ending now instead of a capture, e.g. 2h, 90m")
    g.add_argument("--start", metavar="TIME", help="time range start: ISO 8601 (e.g. 2026-10-17T10:00+07:00) or now-2h")
    g.add_argument("--end", metavar="TIME", help="time range end (default: now)")
    g.add_argument("--split", choices=["train", "test"],
                   help="train = 07 training captures, test = start-live demo captures")
    g.add_argument("--lookback", metavar="AGE", help="how far back to look for captures (default 7d)")
    g.add_argument("--loki", metavar="URL", help="Loki address (default: $LOKI_URL, else http://127.0.0.1:3100)")
    g.add_argument("--yes", action="store_true", help="no questions: needs a capture choice and (for loop) --model")


def _model_options(p, required=False):
    g = p.add_argument_group("the new MODEL")
    g.add_argument("--model", metavar="NAME", required=required,
                   help="folder name under src/artifacts/runs/ (default: model-<experiment>); never overwritten")
    g.add_argument("--seeds", type=int, default=3, metavar="N",
                   help="train N times with different random seeds and report mean ± std (default 3; 1 = quick test)")
    g.add_argument("--epochs", type=int, metavar="N", help="max training epochs per seed (default 100, early stop)")
    g.add_argument("--benign-split", choices=list(SPLITS), default="train",
                   help="which built split to train on (default train). test = an idle Loki range pulled with"
                        " --split test; you assert it holds no attack (recorded in lineage.json)")
    g.add_argument("--exclude-file", action="append", default=[], metavar="FILE",
                   help='leave out every window overlapping a range in FILE (JSON {"ranges": [{"start", "end", "reason"}]},'
                        " repeatable); recorded in lineage.json")
    g.add_argument("--trainer-args", metavar='"..."', default="",
                   help='any other train_gnn_models.py flags as one quoted string, e.g. "--hidden 64"')


def build_parser():
    ap = argparse.ArgumentParser(prog="lab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")

    s = _parser(sub, "status", "what you have: current model, experiments, models",
                "Show the current model (and whether its converter still matches), the experiments under\n"
                "src/out/etw/experiments (with how many minutes each provider covers), and the models.")
    s.add_argument("--limit", type=int, default=10, metavar="N", help="show the N newest experiments/models")

    lp = _parser(sub, "loop", "capture -> experiment -> model in one go",
                 "Pull a capture from Loki, build it into an experiment, compute features and train a new\n"
                 "model. Same as `lab pull` followed by `lab train`. Does NOT switch the live server.", EX_LOOP)
    _capture_options(lp)
    _model_options(lp)

    pp = _parser(sub, "pull", "capture -> experiment (pull from Loki + build)",
                 "Pull a capture from Loki into src/out/etw/experiments/<capture>/ and build it: convert to\n"
                 "edges, cut 5-min windows, check, write experiment.json.", EX_PULL)
    _capture_options(pp)
    pp.add_argument("--list", action="store_true", help="only list the captures in Loki")

    tp = _parser(sub, "train", "experiment -> model (features + OCRGCN)",
                 "Compute the 15 behaviour features and train one OCRGCN per node type, all into a new\n"
                 "folder src/artifacts/runs/<model>/ (+ a copy of experiment.json and lineage.json).", EX_TRAIN)
    tp.add_argument("--experiment", required=True, metavar="ID",
                    help="folder name under src/out/etw/experiments/ (see: lab status), or a path")
    _model_options(tp)

    pr = _parser(sub, "promote", "make MODEL the one the live server loads",
                 "Point the live server (ocr-live, serve.sh) at a model made by lab train / lab loop.", EX_PROMOTE)
    pr.add_argument("model", metavar="MODEL", help="folder name under src/artifacts/runs/")
    pr.add_argument("--shared", action="store_true",
                    help="write the tracked src/artifacts/current.json instead of this machine's current.local.json")

    cp = _parser(sub, "compare", "two models on the same replayed windows",
                 "Join the windows.csv of replay output folders on window start, and count red windows per label\n"
                 "(the label file's ranges; every other window is idle). Only windows scored by all models count.",
                 EX_COMPARE)
    cp.add_argument("--baseline", required=True, metavar="DIR", help="sessions dir of the model to beat")
    cp.add_argument("--candidate", required=True, action="append", metavar="DIR", help="sessions dir (repeatable)")
    cp.add_argument("--label-file", action="append", default=[], metavar="FILE",
                    help='JSON {"ranges": [{"start", "end", "label"}]}, repeatable')
    cp.add_argument("--out", metavar="CSV", help="also write every compared window here")

    c = _parser(sub, "current", "print the current model's paths",
                "Print which model the live server loads (used by serve.sh and rejudge.sh).")
    c.add_argument("--field", choices=["run", "run_dir", "scaler", "experiment"], help="print just this path")

    _parser(sub, "selftest", "prove the plumbing runs (fake Loki, tiny model)",
            "Fake Loki <- synthetic capture -> pull -> train (1 seed, 3 epochs) -> promote. Proves it RUNS,\n"
            "not that it detects attacks.")
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.cmd is None:
        ap.print_help()
        return 0
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "pull":
        if a.list:
            return run([os.path.join(PIPE, "loki_pull.py"), "--list",
                        *(["--lookback", a.lookback] if a.lookback else []), *(["--loki", a.loki] if a.loki else [])])
        pull(loki_pull_args(a))
        return 0
    if a.cmd in ("loop", "train"):
        import shlex
        extra = shlex.split(a.trainer_args)
        if a.cmd == "train":
            train(a.experiment, a.model, a.seeds, a.epochs, extra, a.benign_split, a.exclude_file)
            return 0
        if a.yes and not a.model:
            ap.error("loop --yes needs --model NAME (no question is asked)")
        if a.yes and not (a.capture or a.latest or a.since or a.start):
            ap.error("loop --yes needs a capture choice: --capture, --latest, --since or --start")
        exp = pull(loki_pull_args(a))
        model = a.model or (ask_model_name(exp) if sys.stdin.isatty() else None)
        if not model:
            return 1
        train(exp, model, a.seeds, a.epochs, extra, a.benign_split, a.exclude_file)
        return 0
    if a.cmd == "promote":
        promote(a.model, a.shared)
        return 0
    if a.cmd == "compare":
        compare(a.baseline, a.candidate, a.label_file, a.out)
        return 0
    if a.cmd == "current":
        cur = current()
        if not cur:
            return 1
        print(cur.get(a.field) or "" if a.field else json.dumps(cur, indent=1))
        return 0
    if a.cmd == "selftest":
        return selftest()
    return 1


if __name__ == "__main__":
    sys.exit(main())
