"""writer.py - batched line writer: one write + flush per provider file per second.

SilkService opened, wrote and closed its file for every single event (File.AppendAllText), so every event
cost a Create + Write of its own. Here the ETW callback only appends bytes to a list (cheap, in memory); a
timer thread calls flush() once a second, which writes the whole batch with one write() and flush().
Each batch ends on a full line, so a reader (the shipper) never sees half a line.

    w = LineWriter(r"C:\\lab-out\\etw\\raw\\test\\live-20261026T080000Z", ["kproc", "kfile", "knet"])
    w.add("kfile", b'{"ProviderName":...}\\n')     # from the ETW callback thread
    w.flush()                                      # from the timer thread: kfile.json grows by whole lines
"""
from __future__ import annotations

import os
import threading


class LineWriter:
    def __init__(self, out_dir: str, stems: list[str]):
        os.makedirs(out_dir, exist_ok=True)
        self.dir = out_dir
        self.paths = {s: os.path.join(out_dir, s + ".json") for s in stems}
        # "ab": binary append; the file is opened ONCE for the whole run.
        self.fh = {s: open(p, "ab") for s, p in self.paths.items()}
        self.pending: dict[str, list[bytes]] = {s: [] for s in stems}
        self.lines = {s: 0 for s in stems}       # lines written so far
        self.bytes = {s: 0 for s in stems}
        self.lock = threading.Lock()

    def add(self, stem: str, line: bytes) -> None:
        with self.lock:
            self.pending[stem].append(line)

    def flush(self) -> int:
        with self.lock:                          # swap the lists out, write without holding the lock
            batches, self.pending = self.pending, {s: [] for s in self.pending}
        n = 0
        for stem, lines in batches.items():
            if not lines:
                continue
            blob = b"".join(lines)
            self.fh[stem].write(blob)
            self.fh[stem].flush()
            self.lines[stem] += len(lines)
            self.bytes[stem] += len(blob)
            n += len(lines)
        return n

    def close(self) -> None:
        self.flush()
        for fh in self.fh.values():
            fh.close()
