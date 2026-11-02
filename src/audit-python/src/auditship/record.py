"""record.py - one audit.log line -> one Loki entry [ts_ns, line, metadata].

An auditd record (ENRICHED log format, Ubuntu's default) looks like this; \\x1d (ASCII "group separator") splits
the raw kernel fields from the names auditd resolved for us:

    type=SYSCALL msg=audit(1762085657.123:4567): arch=c000003e syscall=59 success=yes ... key="exec"\\x1dARCH=x86_64 SYSCALL=execve ...

    type      = record kind (SYSCALL, EXECVE, PATH, CWD, SOCKADDR, PROCTITLE, ...)
    1762085657.123 = event time, epoch seconds with milliseconds
    4567      = event serial: every record of ONE event (SYSCALL + EXECVE + PATH ... ) shares it
    key       = the -k tag of the rule that fired (only on the SYSCALL / CONFIG_CHANGE record)

The line is shipped unchanged (including \\x1d) so a later converter sees exactly what auditd wrote.
"""
from __future__ import annotations

import re

NS = 1_000_000_000
_HEAD = re.compile(r"^type=(\S+) msg=audit\((\d+)\.(\d+):(\d+)\):")
_KEY = re.compile(r'\bkey=(?:"([^"]*)"|(\(null\)|\S+))')


def parse_head(text: str) -> tuple[str, int, int] | None:
    """'type=SYSCALL msg=audit(1762085657.123:4567): ...' -> ('SYSCALL', 1762085657123000000, 4567)."""
    m = _HEAD.match(text)
    if not m:
        return None
    frac = m.group(3)[:9].ljust(9, "0")              # "123" -> 123000000 ns
    return m.group(1), int(m.group(2)) * NS + int(frac), int(m.group(4))


def rule_key(text: str) -> str:
    """The rule's -k tag, or '' when the record has none (EXECVE, PATH, ...) or key=(null)."""
    m = _KEY.search(text.split("\x1d", 1)[0])
    if not m:
        return ""
    k = m.group(1) if m.group(1) is not None else m.group(2)
    return "" if k == "(null)" else k


def entry(raw: bytes, now_ns: int) -> tuple[list, bool]:
    """One file line -> [ts_ns, line, {type, key}]. A line without an audit(...) header keeps the read time
    (same rule as etwcollect.loki.entry) and is counted as bad."""
    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    head = parse_head(text)
    if head is None:
        return [str(now_ns), text], False
    rtype, ts, _serial = head
    meta = {"type": rtype}
    key = rule_key(text)
    if key:
        meta["key"] = key
    return [str(ts), text, meta], True
