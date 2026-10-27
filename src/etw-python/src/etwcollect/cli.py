"""cli.py - `etwcollect <command>` (console script from pyproject.toml).

    etwcollect run [--state FILE | --out-dir DIR] [--no-push]   capture until stopped (the SYSTEM task runs this)
    etwcollect stop [--wait 60]                                stop the session; waits until the collector exited
    etwcollect status                                          collector-status.json + live ETW counters
    etwcollect dosmap [--out FILE]                             this machine's \\device\\... -> c: map (JSON)
    etwcollect push-ledger --exp ID --dir DIR                  07's workload ledger CSVs -> Loki
    etwcollect selftest [--live]                               pure parts (any OS); --live: a real 15 s session

Settings come from etwlab.json (default C:\\ProgramData\\etwlab\\etwlab.json, or $ETWLAB_CONFIG), written by
the win_collector Ansible role: Collectors (Stem, Provider, Keywords), RawRoot, LokiUrl, Host. The run folder
comes from state.json next to it, written by `etwctl start`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

DEFAULT_HOME = r"C:\ProgramData\etwlab"


def load_config(path: str | None) -> tuple[dict, str]:
    path = path or os.environ.get("ETWLAB_CONFIG") or os.path.join(DEFAULT_HOME, "etwlab.json")
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh), os.path.dirname(os.path.abspath(path))


def cmd_run(a) -> int:
    from .collector import Config, Collector
    from .providers import Provider
    cfg, home = load_config(a.config)
    if a.out_dir:
        out_dir = os.path.abspath(a.out_dir)
    else:
        with open(a.state or os.path.join(home, "state.json"), encoding="utf-8-sig") as fh:
            st = json.load(fh)
        out_dir = st["dir"]
    run = os.path.basename(out_dir.rstrip("\\/"))
    split = os.path.basename(os.path.dirname(out_dir.rstrip("\\/")))
    providers = [Provider.from_config(c) for c in cfg["Collectors"]]
    c = Config(out_dir=out_dir, split=split, run=run, providers=providers, raw_root=cfg["RawRoot"],
               status_path=a.status or os.path.join(home, "collector-status.json"),
               positions_path=os.path.join(home, "ship-positions.json"),
               host=cfg.get("Host", "ws-etw"), loki_url=cfg.get("LokiUrl"), push=not a.no_push,
               session=a.session, exclude_images=tuple(a.exclude_image), drain_s=a.drain_s)
    return Collector(c).run()


def _read_status(home: str) -> dict | None:
    try:
        with open(os.path.join(home, "collector-status.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def cmd_stop(a) -> int:
    from .session import RealtimeSession
    _, home = load_config(a.config)
    before = _read_status(home)
    counters = RealtimeSession.stop_by_name(a.session)
    if counters is None:
        print(f"[etwcollect] no session {a.session!r} running")
        return 0
    print(f"[etwcollect] stopped session {a.session!r}: {json.dumps(counters)}")
    deadline = time.monotonic() + a.wait
    while time.monotonic() < deadline:        # the collector flushes and drains to Loki, then writes "stopped"
        st = _read_status(home)
        if st and st.get("state") == "stopped" and (before is None or st.get("updated_utc") != before.get("updated_utc")):
            print(f"[etwcollect] collector finished: {st.get('run')} lines "
                  + ", ".join(f"{k}={v.get('lines')}" for k, v in st.get("providers", {}).items()))
            return 0
        time.sleep(1)
    print(f"[etwcollect] collector did not report 'stopped' within {a.wait:.0f} s", file=sys.stderr)
    return 1


def cmd_status(a) -> int:
    from .session import RealtimeSession
    _, home = load_config(a.config)
    st = _read_status(home)
    live = RealtimeSession(a.session).query() if os.name == "nt" else None
    print(json.dumps({"status": st, "session_running": live is not None, "etw": live}, indent=1))
    return 0 if live is not None else 3


def cmd_dosmap(a) -> int:
    from . import dosmap
    m = dosmap.dos_device_map()
    if a.out:
        dosmap.write_meta(a.out, m)
    else:
        print(json.dumps(m, indent=1))
    return 0


def cmd_push_ledger(a) -> int:
    from .loki import LokiClient, LokiError, push_ledger
    cfg, _ = load_config(a.config)
    url, host = a.loki_url or cfg.get("LokiUrl"), a.host or cfg.get("Host", "ws-etw")
    if not url:
        print("[etwcollect] no LokiUrl in etwlab.json and no --loki-url", file=sys.stderr)
        return 2
    for attempt in range(6):
        try:
            n = push_ledger(LokiClient(url), host, a.exp, a.dir)
            print(f"[etwcollect] ledger: {n} rows -> {url} (job=workload-ledger, exp={a.exp})")
            return 0
        except LokiError as e:
            if not e.retryable or attempt == 5:
                print(f"[etwcollect] ledger push failed: {e}  (re-run: etwcollect push-ledger --exp {a.exp} "
                      f"--dir {a.dir})", file=sys.stderr)
                return 1
            time.sleep(2 ** attempt)
    return 1


def cmd_selftest(a) -> int:
    from . import selftest
    return selftest.live() if a.live else selftest.offline()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="etwcollect", description=__doc__.split("\n")[0])
    ap.add_argument("--config", help="etwlab.json (default $ETWLAB_CONFIG or C:\\ProgramData\\etwlab\\etwlab.json)")
    ap.add_argument("--session", default="etwlab-collector", help="ETW session name")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="capture until `etwcollect stop`")
    r.add_argument("--state", help="state.json from `etwctl start` (default: next to etwlab.json)")
    r.add_argument("--out-dir", help="write here instead of the run folder in state.json")
    r.add_argument("--status", help="status file (default: collector-status.json next to etwlab.json)")
    r.add_argument("--no-push", action="store_true", help="files only, nothing to Loki")
    r.add_argument("--exclude-image", action="append", default=[],
                   help="also drop the I/O of processes whose image name starts with this (repeatable)")
    r.add_argument("--drain-s", type=float, default=30.0, help="seconds to finish shipping after a stop")
    s = sub.add_parser("stop")
    s.add_argument("--wait", type=float, default=60.0)
    sub.add_parser("status")
    d = sub.add_parser("dosmap")
    d.add_argument("--out")
    lg = sub.add_parser("push-ledger")
    lg.add_argument("--exp", required=True, help="experiment id (07's folder name, run x-<id>)")
    lg.add_argument("--dir", required=True, help=r"<exp>\labels")
    lg.add_argument("--loki-url")
    lg.add_argument("--host")
    t = sub.add_parser("selftest")
    t.add_argument("--live", action="store_true", help="Windows, elevated: a real 15 s ETW session")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "stop": cmd_stop, "status": cmd_status, "dosmap": cmd_dosmap,
            "push-ledger": cmd_push_ledger, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
