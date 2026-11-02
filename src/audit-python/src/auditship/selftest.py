"""selftest.py - proves "it runs": parsing, offsets, rotation, Loki outage. Never that anything is detected."""
from __future__ import annotations

import os
import tempfile

from etwcollect.loki import LokiError

from .record import entry, parse_head, rule_key
from .tail import AuditTail

SYSCALL = ('type=SYSCALL msg=audit(1762085657.123:4567): arch=c000003e syscall=59 success=yes exit=0 '
           'pid=1234 uid=1000 comm="curl" exe="/usr/bin/curl" key="exec"\x1dARCH=x86_64 SYSCALL=execve UID="it2102"')
EXECVE = 'type=EXECVE msg=audit(1762085657.123:4567): argc=2 a0="curl" a1="example.com"'


class StubLoki:
    def __init__(self):
        self.lines, self.down = [], False

    def push(self, streams):
        if self.down:
            raise LokiError(0, "connection refused")
        for s in streams:
            assert s["stream"] == {"job": "auditd", "host": "selftest"}, s["stream"]
            self.lines += [v[1] for v in s["values"]]
        return 204


def _append(path: str, *lines: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.writelines(l + "\n" for l in lines)


def run() -> int:
    # 1. one record -> one entry
    assert parse_head(SYSCALL) == ("SYSCALL", 1762085657_123_000_000, 4567)
    assert rule_key(SYSCALL) == "exec" and rule_key(EXECVE) == ""
    assert rule_key('type=SYSCALL msg=audit(1.0:1): key=(null)') == ""
    v, ok = entry(SYSCALL.encode() + b"\n", now_ns=0)
    assert ok and v[0] == "1762085657123000000" and v[2] == {"type": "SYSCALL", "key": "exec"} and v[1] == SYSCALL
    v, ok = entry(b"garbage\n", now_ns=42)
    assert not ok and v == ["42", "garbage"]

    with tempfile.TemporaryDirectory() as d:
        log, pos = os.path.join(d, "audit.log"), os.path.join(d, "positions.json")
        loki = StubLoki()
        quiet = lambda m: None
        t = AuditTail(loki, log, pos, "selftest", log=quiet)

        # 2. tail: a partial last line waits until it is complete
        _append(log, SYSCALL, EXECVE)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("type=PROCTITLE msg=audit(1762085657.123:4567): proctitle=6375")
        assert t.step() == 2 and loki.lines == [SYSCALL, EXECVE]
        assert t.step() == 0
        _append(log, "726C")
        assert t.step() == 1 and loki.lines[-1].endswith("proctitle=6375726C")   # joined, shipped once

        # 3. Loki down: offset stays, nothing lost after recovery
        _append(log, EXECVE)
        loki.down = True
        assert t.step() == 0
        loki.down, t.backoff_until = False, 0.0
        assert t.step() == 1 and len(loki.lines) == 4

        # 4. rotation: unshipped tail of the old file comes first, then the new file from 0
        _append(log, SYSCALL)                         # written before the rename, not shipped yet
        os.rename(log, log + ".1")
        _append(log, EXECVE)
        assert t.step() == 1 and loki.lines[-1] == SYSCALL      # old file's tail
        assert t.step() == 1 and loki.lines[-1] == EXECVE       # then the new file
        assert len(loki.lines) == 6

        # 5. restart: positions survive, nothing is sent twice
        t2 = AuditTail(loki, log, pos, "selftest", log=quiet)
        assert t2.step() == 0 and len(loki.lines) == 6
        _append(log, SYSCALL)
        assert t2.step() == 1 and len(loki.lines) == 7

    print("auditship selftest: OK (parse, partial line, Loki outage, rotation, restart)")
    return 0
