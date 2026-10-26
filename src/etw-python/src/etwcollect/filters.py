"""filters.py - drop the collector's own I/O BEFORE it becomes a line (the fix for the SilkService loop).

Why: every line we write is itself a Kernel-File Write; every read of our files (the shipper, formerly Alloy)
is a Kernel-File Read. Written out, those become more lines -> more events (docs/notes/silkservice-kfile-lag.md).
Three rules, each counted (Excluder.counts -> collector-status.json):

  (a) self_pid      the event's header PID is ours (or, during the switch, Alloy's).  kproc / kfile.
  (b) own_file      a Kernel-File event on one of OUR files, whoever issued it: a Create whose FileName is under
                    the raw output root, or a Read/Write on a FileObject / FileKey learned from such a Create.
                    This also catches System (PID 4) lazy-writer flushes of our files, which (a) cannot.
  (c) net_pid       a Kernel-Network event whose PAYLOAD PID is excluded. The header PID of network events is
                    whatever thread ran (Idle, System, any process: 77 % mismatch measured), so it is not used.

FileObject = the kernel address of one open handle's file object; FileKey = the address of the file's stream
control block, shared by every handle to the same file. Both arrive as hex strings ("0xffffcd02...").
A FileObject address is reused after a close; a new Create on that address always comes first, so a Create
for a path outside our root forgets it again.
"""
from __future__ import annotations

from collections import Counter


def image_base(path: str) -> str:
    """'\\Device\\HarddiskVolume4\\Windows\\System32\\msedge.exe' -> 'msedge' (SilkETW's ProcessName shape)."""
    b = path.replace("/", "\\").rsplit("\\", 1)[-1]
    return b[:-4] if b.lower().endswith(".exe") else b


class Excluder:
    def __init__(self, own_pids: set[int], image_prefixes=(), own_paths=()):
        self.own: set[int] = set(own_pids)                 # never dropped from the excluded set
        self.pids: set[int] = set(own_pids)                # own + PIDs of excluded images (alloy*)
        self.image_prefixes = tuple(p.lower() for p in image_prefixes)
        # own_paths: NT device paths, lower-case. A folder ends with '\\' (prefix match); a file does not.
        self.own_paths = tuple(p.lower() for p in own_paths)
        self.fobj: set[str] = set()                        # FileObjects of our files
        self.fkey: set[str] = set()                        # FileKeys of our files
        self.counts: Counter = Counter()                   # "kfile.own_file" -> n

    # ---------------------------------------------------------------- process names
    def note_process(self, pid: int, name: str) -> None:
        """A process started (or was found at startup) with this image name."""
        if pid in self.own:
            return
        if self.image_prefixes and name.lower().startswith(self.image_prefixes):
            self.pids.add(pid)
        else:
            self.pids.discard(pid)                         # the PID was reused by another image

    def ours(self, filename: str) -> bool:
        f = filename.lower()
        return any(f.startswith(p) if p.endswith("\\") else f == p for p in self.own_paths)

    # ---------------------------------------------------------------- rules
    def header(self, stem: str, pid: int) -> bool:
        """Rule (a) for kproc, checked before any decoding."""
        if pid in self.pids:
            self.counts[stem + ".self_pid"] += 1
            return True
        return False

    def kfile(self, event: str, pid: int, d: dict[str, str]) -> bool:
        """Rules (a) + (b) for one decoded Kernel-File event. True = drop it."""
        fo = d.get("FileObject", "").lower()
        fk = d.get("FileKey", "").lower()
        if event.startswith("Create"):                     # Create / CreateNewFile carry the path
            if self.ours(d.get("FileName", "")):
                if fo:
                    self.fobj.add(fo)
                self.counts["kfile.own_file"] += 1
                return True
            self.fobj.discard(fo)                          # address reused for another file
        elif (fo and fo in self.fobj) or (fk and fk in self.fkey):
            if fo in self.fobj and fk:
                self.fkey.add(fk)                          # learn the file's key from a handle we know
            self.counts["kfile.own_file"] += 1
            return True
        if pid in self.pids:
            self.counts["kfile.self_pid"] += 1
            return True
        return False

    def knet(self, d: dict[str, str]) -> bool:
        """Rule (c): the owner is the payload PID."""
        try:
            owner = int(d.get("PID", ""))
        except ValueError:
            return False
        if owner in self.pids:
            self.counts["knet.net_pid"] += 1
            return True
        return False

    def reset_files(self) -> None:
        """New run folder: file objects of the old run mean nothing any more."""
        self.fobj.clear()
        self.fkey.clear()
