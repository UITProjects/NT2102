"""lab - the improve loop in one command: pull from Loki -> build -> features -> train -> promote.

Setup once (repo root):  uv sync ; source .venv/bin/activate      -> `lab` is on PATH (or: uv run lab ...)

    lab status                         current model, experiments, runs; flags short provider coverage
    lab pull   [loki_pull flags]       Loki -> src/out/etw/experiments/<id>/ and build it (convert, window, check)
    lab train  --exp <id> [--run NAME] [-- train_gnn_models flags]
                                       features + OCRGCN into ONE run folder src/artifacts/runs/<run>/
    lab loop   [loki_pull flags] [--run NAME]
                                       pull + train in one go (interactive when no flags)
    lab promote <run> [--shared]       make <run> the model the live server loads
    lab current [--field run_dir]      print the current-model pointer (used by serve.sh / rejudge.sh)
    lab selftest

Every stage still runs the existing script as its own process and writes standalone files (CLAUDE.md §6):
loki_pull.py, build_experiment.py, encode_to_features.py, train_gnn_models.py.

Run folder (everything one model needs; nothing shared is overwritten):
    src/artifacts/runs/<run>/experiment.json      the training data's record (converter sha, windowing, pull)
                             features/            features_raw.csv, features_model.csv, feature_scaler.json
                             models/ train_meta.json train_scores.csv runs_summary.csv   (train_gnn_models)
                             lineage.json         which experiment, commands, git commit, tool sha256s

Current model: src/artifacts/current.json (in git) or, if present, current.local.json (gitignored; what
`lab promote` writes, so a machine can switch models without touching tracked files).
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
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "src")
PIPE = os.path.join(SRC, "pipeline")
EXPS = os.path.join(SRC, "out", "etw", "experiments")
RUNS = os.path.join(SRC, "artifacts", "runs")
POINTER = os.path.join(SRC, "artifacts", "current.json")
LOCAL = os.path.join(SRC, "artifacts", "current.local.json")
CONVERTER = os.path.join(PIPE, "etw2nodlink.py")
EDGES = "SimulatedW10_train_edges.csv"
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
    print("\nexperiments  (src/out/etw/experiments; provider coverage = minutes with events / capture minutes)")
    for d in sorted(glob.glob(os.path.join(EXPS, "*")), key=os.path.getmtime, reverse=True)[:a.limit]:
        if not os.path.isdir(d):
            continue
        e = os.path.join(d, "experiment.json")
        rec = json.load(open(e, encoding="utf-8-sig")) if os.path.exists(e) else {}
        res = rec.get("results", {})
        cov, span = coverage(d)
        flags = [p for p, (m, _, _) in cov.items() if span and m < 0.5 * span]
        built = "built" if os.path.exists(os.path.join(d, EDGES)) else "NOT built"
        print(f"  {os.path.basename(d):<26} {rec.get('source', 'vm-07'):<9} {built:<9} windows {res.get('windows', '-')!s:<3} "
              + " ".join(f"{p} {m:.0f}/{span:.0f}m" for p, (m, _, _) in cov.items())
              + (f"   WARNING short coverage: {', '.join(flags)}" if flags else ""))
    print("\nruns  (src/artifacts/runs)")
    for d in sorted(glob.glob(os.path.join(RUNS, "*")), key=os.path.getmtime, reverse=True)[:a.limit]:
        lin = os.path.join(d, "lineage.json")
        src = json.load(open(lin)).get("experiment", "?") if os.path.exists(lin) else "(no lineage.json)"
        mark = "*" if c and os.path.abspath(d) == os.path.abspath(c.get("run_dir") or "") else " "
        print(f" {mark}{os.path.basename(d):<26} from {src}   converter_check {converter_check(os.path.join(d, 'experiment.json'))}")
    return 0


# --------------------------------------------------------------------------- pull / train / loop
def cmd_pull(rest):
    """loki_pull.py with --build; -> the experiment folder it produced (newest new/changed folder)."""
    before = {d: os.path.getmtime(d) for d in glob.glob(os.path.join(EXPS, "*"))}
    args = list(rest)
    if "--build" not in args:
        args.append("--build")
    run([os.path.join(PIPE, "loki_pull.py"), *args])
    after = [d for d in glob.glob(os.path.join(EXPS, "*"))
             if os.path.isdir(d) and (d not in before or os.path.getmtime(d) > before[d])]
    if not after:
        sys.exit("[lab] the pull produced no experiment folder")
    return max(after, key=os.path.getmtime)


def train(exp_id, run_name=None, trainer_args=(), runs_root=None):
    runs_root = runs_root or RUNS
    exp = exp_id if os.path.isdir(exp_id) else os.path.join(EXPS, exp_id)
    edges = os.path.join(exp, EDGES)
    if not os.path.exists(edges):
        sys.exit(f"[lab] {edges} missing: build the experiment first (lab pull, or build_experiment.py --exp)")
    run_dir = os.path.join(runs_root, run_name or "run-" + os.path.basename(os.path.normpath(exp)))
    if os.path.exists(run_dir):
        sys.exit(f"[lab] {rel(run_dir)} exists; choose another --run (a trained run is never overwritten)")
    feats = os.path.join(run_dir, "features")
    cmds = [[os.path.join(PIPE, "encode_to_features.py"), "--edges", edges, "--out-dir", feats],
            [os.path.join(PIPE, "train_gnn_models.py"), "--edges", edges,
             "--features", os.path.join(feats, "features_model.csv"),
             "--scaler", os.path.join(feats, "feature_scaler.json"), "--out-dir", run_dir, *trainer_args]]
    os.makedirs(run_dir)
    try:
        for c in cmds:
            run(c)
    except SystemExit:
        failed = run_dir + ".failed"
        shutil.rmtree(failed, ignore_errors=True)
        os.replace(run_dir, failed)
        print(f"[lab] training failed; partial output kept in {rel(failed)}")
        raise
    shutil.copy2(os.path.join(exp, "experiment.json"), os.path.join(run_dir, "experiment.json"))
    rec = json.load(open(os.path.join(exp, "experiment.json"), encoding="utf-8-sig"))
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "src/pipeline"], cwd=REPO,
                           capture_output=True, text=True).stdout.strip()
    lineage = {"run": os.path.basename(run_dir), "experiment": os.path.basename(os.path.normpath(exp)),
               "experiment_source": rec.get("source"), "created_utc": datetime.now(timezone.utc).isoformat(),
               "git_commit": git.stdout.strip() or None, "pipeline_uncommitted_changes": bool(dirty),
               "converter_sha256": rec.get("tools", {}).get("converter"),
               "tools": {os.path.basename(p): sha(os.path.join(PIPE, p))
                         for p in ("encode_to_features.py", "train_gnn_models.py")},
               "commands": [" ".join(rel(x) if os.path.isabs(x) else x for x in c) for c in cmds]}
    with open(os.path.join(run_dir, "lineage.json"), "w") as fh:
        json.dump(lineage, fh, indent=1)
    print(f"[lab] run ready: {rel(run_dir)}   next: lab promote {os.path.basename(run_dir)}")
    return run_dir


def split_rest(argv):
    """'... -- x y' -> (before, after)."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def cmd_loop(rest):
    pull_args, trainer_args = split_rest(rest)
    run_name = None
    if "--run-name" in pull_args:
        i = pull_args.index("--run-name")
        run_name = pull_args[i + 1]
        del pull_args[i:i + 2]
    exp = cmd_pull(pull_args)
    if run_name is None and sys.stdin.isatty() and "--yes" not in pull_args:
        import questionary
        run_name = questionary.text("Name of the new model run:", default="run-" + os.path.basename(exp)).ask()
        if not run_name:
            return 1
    train(exp, run_name, trainer_args)
    return 0


def promote(run, shared=False):
    run_dir = run if os.path.isdir(run) else os.path.join(RUNS, run)
    rec = pointer_for(run_dir)
    for k in ("scaler", "experiment"):
        if not os.path.exists(os.path.join(REPO, rec[k])):
            sys.exit(f"[lab] {rec[k]} missing: only runs made by `lab train` can be promoted this way")
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
            exp = cmd_pull(["--loki", fake.url, "--latest", "--yes", "--out-root", EXPS, "--lookback", "3650d"])
            assert os.path.exists(os.path.join(exp, EDGES)), exp
            rd = train(os.path.basename(exp), "t1", ["--runs", "1", "--epochs", "3"])
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
    finally:
        fake.close()
        EXPS, RUNS, POINTER, LOCAL = saved
    print("[SELFTEST OK] Loki -> pull + build -> features + train into one run folder -> promote -> current")
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


# --------------------------------------------------------------------------- main
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("pull", "loop"):          # everything after the subcommand goes to loki_pull
        if argv[0] == "pull":
            cmd_pull(argv[1:])
            return 0
        return cmd_loop(argv[1:])
    ap = argparse.ArgumentParser(prog="lab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--limit", type=int, default=10)
    sub.add_parser("pull", help="loki_pull.py flags pass through; --build is added")
    sub.add_parser("loop", help="pull + train; loki_pull flags, --run-name NAME, then -- trainer flags")
    t = sub.add_parser("train")
    t.add_argument("--exp", required=True, help="experiment id under src/out/etw/experiments (or a path)")
    t.add_argument("--run", help="run folder name (default run-<exp>)")
    t.add_argument("trainer_args", nargs=argparse.REMAINDER, help="after --: train_gnn_models.py flags")
    p = sub.add_parser("promote")
    p.add_argument("run")
    p.add_argument("--shared", action="store_true", help="write the tracked current.json instead of current.local.json")
    c = sub.add_parser("current")
    c.add_argument("--field", choices=["run", "run_dir", "scaler", "experiment"])
    sub.add_parser("selftest")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "train":
        train(a.exp, a.run, [x for x in a.trainer_args if x != "--"])
        return 0
    if a.cmd == "promote":
        promote(a.run, a.shared)
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
