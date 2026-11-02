"""cli.py - `auditship <command>` (console script from pyproject.toml).

    auditship run --loki-url URL [--host NAME] [--log FILE] [--state-dir DIR]   ship until stopped (systemd runs this)
    auditship status [--state-dir DIR]                                           the last status.json the run wrote
    auditship selftest                                                           temp log + fake rotation + stub Loki

State dir (default /var/lib/auditship): positions.json (where we are in audit.log) and status.json (counters,
rewritten every 5 s). The systemd unit runs as the `auditship` user, which the audit rules exclude (D11).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time

DEFAULT_LOG = "/var/log/audit/audit.log"
DEFAULT_STATE = "/var/lib/auditship"


def cmd_run(a) -> int:
    from etwcollect.loki import LokiClient

    from .tail import AuditTail
    os.makedirs(a.state_dir, exist_ok=True)
    t = AuditTail(LokiClient(a.loki_url), a.log, os.path.join(a.state_dir, "positions.json"),
                  a.host or socket.gethostname(), log=lambda m: print(m, flush=True))
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    print(f"[ship] {a.log} -> {a.loki_url} as host={t.labels['host']}", flush=True)
    last_status = 0.0
    while not stop:
        if t.step() == 0:
            time.sleep(1.0)                           # caught up (or Loki backing off): poll once a second
        if time.monotonic() - last_status >= 5:
            last_status = time.monotonic()
            tmp = os.path.join(a.state_dir, "status.json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(t.status() | {"written_ns": time.time_ns()}, fh, indent=1)
            os.replace(tmp, os.path.join(a.state_dir, "status.json"))
    print("[ship] stopped", flush=True)
    return 0


def cmd_status(a) -> int:
    try:
        with open(os.path.join(a.state_dir, "status.json"), encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"no status yet ({e})")
        return 1
    st["age_s"] = round((time.time_ns() - st.pop("written_ns")) / 1e9, 1)
    print(json.dumps(st, indent=1))
    return 0


def cmd_selftest(a) -> int:
    from . import selftest
    return selftest.run()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="auditship")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--loki-url", required=True, help="e.g. http://10.21.3.20:3100")
    r.add_argument("--host", help="Loki host label (default: this machine's hostname)")
    r.add_argument("--log", default=DEFAULT_LOG)
    r.add_argument("--state-dir", default=DEFAULT_STATE)
    s = sub.add_parser("status")
    s.add_argument("--state-dir", default=DEFAULT_STATE)
    sub.add_parser("selftest")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
