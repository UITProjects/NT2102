"""tail.py - follow audit.log across rotations and push whole lines to Loki, losing nothing.

Positions file (atomic rewrite after every accepted batch):
    {"inode": 1310723, "offset": 52311}

Why the inode: auditd rotates by renaming audit.log -> audit.log.1 and opening a NEW audit.log. The path stays
the same but the file behind it changes; the inode number identifies the file we were reading. On a rotation we
first finish the old file (now audit.log.1, found by its inode) from the saved offset, then start the new one
at offset 0. The offset only moves after Loki answered 204, so a Loki outage delays lines but does not lose them.
Lost only if auditd rotates TWICE while Loki is down (the old file is then audit.log.2, which we also check) or
the backlog falls out of num_logs entirely; that is logged.
"""
from __future__ import annotations

import json
import os
import time

from etwcollect.loki import LokiClient, LokiError

from .record import entry

MAX_BATCH = 1 << 20          # 1 MiB of line bytes per push (same as etwcollect)


class AuditTail:
    def __init__(self, client: LokiClient | None, log_path: str, positions_path: str, host: str,
                 max_batch: int = MAX_BATCH, now_ns=time.time_ns, log=print):
        self.client, self.log_path, self.pos_path, self.host = client, log_path, positions_path, host
        self.max_batch, self.now_ns, self.log = max_batch, now_ns, log
        self.labels = {"job": "auditd", "host": host}
        self.state = {"inode": None, "offset": 0}
        if os.path.exists(positions_path):
            try:
                with open(positions_path, encoding="utf-8") as fh:
                    self.state.update(json.load(fh))
            except (OSError, ValueError):
                self.log(f"[ship] positions file unreadable, starting over: {positions_path}")
        self.pushed_lines = self.bad_lines = self.rejected = 0
        self.last_push_ns = self.last_error = None
        self.backoff_until, self.backoff = 0.0, 1.0

    # ------------------------------------------------------------------ bookkeeping
    def _save(self) -> None:
        tmp = self.pos_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh)
        os.replace(tmp, self.pos_path)

    def _rotated_path(self, inode: int) -> str | None:
        """Where the file we were reading went after a rotation (audit.log.1, .2, ...), by inode."""
        for n in range(1, 10):
            p = f"{self.log_path}.{n}"
            try:
                if os.stat(p).st_ino == inode:
                    return p
            except OSError:
                pass
        return None

    def backlog(self) -> int:
        """Unshipped bytes in the current file (old rotated leftovers not counted)."""
        try:
            st = os.stat(self.log_path)
        except OSError:
            return 0
        return max(0, st.st_size - self.state["offset"]) if st.st_ino == self.state["inode"] else st.st_size

    # ------------------------------------------------------------------ pushing
    def _push(self, values: list) -> bool:
        """True = Loki has the lines (or refused them for good); False = try again later."""
        if self.client is None:
            return False
        try:
            self.client.push([{"stream": self.labels, "values": values}])
            self.last_push_ns, self.last_error, self.backoff = self.now_ns(), None, 1.0
            return True
        except LokiError as e:
            self.last_error = str(e)
            if not e.retryable:                       # e.g. older than Loki's 3-day window: skip for good
                self.rejected += 1
                self.log(f"[ship] Loki refused a batch, skipping it: {e}")
                return True
            self.backoff_until = time.monotonic() + self.backoff
            self.backoff = min(self.backoff * 2, 30.0)
            return False

    def _ship_from(self, path: str, final: bool) -> tuple[int, bool]:
        """Ship one batch of `path` from state.offset. final = the file no longer grows (rotated away), so a
        half line at its end is complete. Returns (lines accepted, reached the end)."""
        with open(path, "rb") as fh:
            fh.seek(self.state["offset"])
            chunk = fh.read(self.max_batch)
        if not chunk:
            return 0, True
        cut = chunk.rfind(b"\n") + 1                  # whole lines only; a partial last line waits
        if cut == 0:
            if len(chunk) == self.max_batch or final:
                cut = len(chunk)
            else:
                return 0, True
        now = self.now_ns()
        values = []
        for raw in chunk[:cut].splitlines():
            if raw.strip():
                v, ok = entry(raw, now)
                values.append(v)
                self.bad_lines += not ok
        refused = self.rejected
        if values and not self._push(values):
            return 0, False                           # Loki down: keep the offset, retry after backoff
        self.state["offset"] += cut
        self._save()
        if self.rejected == refused:
            self.pushed_lines += len(values)
            return len(values), False
        return 0, False

    def step(self) -> int:
        """Ship at most one batch. Returns the number of lines Loki accepted."""
        if time.monotonic() < self.backoff_until:
            return 0
        try:
            st = os.stat(self.log_path)
        except OSError:
            return 0                                  # auditd not started yet / mid-rotation
        if self.state["inode"] is None:               # first start: ship what auditd has kept so far
            self.state.update(inode=st.st_ino, offset=0)
            self._save()
        if st.st_ino != self.state["inode"]:          # rotated: finish the old file first
            old = self._rotated_path(self.state["inode"])
            if old is not None:
                sent, done = self._ship_from(old, final=True)
                if not done:
                    return sent
            else:
                self.log(f"[ship] rotated file with inode {self.state['inode']} is gone; its unshipped tail is lost")
            self.state.update(inode=st.st_ino, offset=0)
            self._save()
        elif st.st_size < self.state["offset"]:       # truncated in place (not auditd's way, but be safe)
            self.log("[ship] audit.log shrank, restarting at offset 0")
            self.state["offset"] = 0
            self._save()
        sent, _ = self._ship_from(self.log_path, final=False)
        return sent

    def status(self) -> dict:
        return {"log": self.log_path, "inode": self.state["inode"], "offset": self.state["offset"],
                "backlog_bytes": self.backlog(), "pushed_lines": self.pushed_lines, "bad_lines": self.bad_lines,
                "rejected_batches": self.rejected, "last_push_ns": self.last_push_ns, "last_error": self.last_error}
