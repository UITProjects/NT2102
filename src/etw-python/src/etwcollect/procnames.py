"""procnames.py - PID -> process name ('msedge', SilkETW's ProcessName: image file name without '.exe').

ETW event headers carry only a PID. SilkETW asked .NET for the name when it wrote the line, so a process that
had already exited got 'N/A' (9,487 of 17k kproc lines on 2026-10-25). Here the map is filled
  1. at startup from a process snapshot (CreateToolhelp32Snapshot: every running process, its exe name),
  2. on every Kernel-Process ProcessStart event, from its ImageName field (or, if missing, by asking Windows
     while the new process is still alive: QueryFullProcessImageNameW),
so short-lived processes such as `whoami` keep their real name. A PID is overwritten when it is reused.
"""
from __future__ import annotations

import ctypes
import os

from .filters import image_base


class ProcNames:
    def __init__(self):
        self.names: dict[int, str] = {0: "Idle", 4: "System"}
        self.misses: set[int] = set()              # PIDs we already failed to look up (dead): no retry

    def set(self, pid: int, image: str) -> str:
        name = image_base(image) if image else ""
        if name:
            self.names[pid] = name
            self.misses.discard(pid)
        return name

    def get(self, pid: int) -> str:
        n = self.names.get(pid)
        if n is not None:
            return n
        if pid in self.misses:
            return "N/A"
        n = query_image(pid)
        if n:
            self.names[pid] = image_base(n)
            return self.names[pid]
        self.misses.add(pid)
        return "N/A"

    def snapshot(self) -> list[tuple[int, str]]:
        """Fill from the running processes. Returns [(pid, name)] so the Excluder can see them too."""
        found = []
        for pid, exe in snapshot():
            if pid in (0, 4):
                continue
            found.append((pid, self.set(pid, exe)))
        return found


def snapshot() -> list[tuple[int, str]]:
    if os.name != "nt":
        return []
    from . import winapi as w
    h = w.CreateToolhelp32Snapshot(w.TH32CS_SNAPPROCESS, 0)
    if not h or h == w.INVALID_HANDLE_VALUE:
        return []
    out = []
    try:
        e = w.PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = w.Process32FirstW(h, ctypes.byref(e))
        while ok:
            out.append((int(e.th32ProcessID), e.szExeFile))
            ok = w.Process32NextW(h, ctypes.byref(e))
    finally:
        w.CloseHandle(h)
    return out


def query_image(pid: int) -> str:
    """Full image path of a live process ('C:\\Windows\\System32\\whoami.exe'), '' when it is gone."""
    if os.name != "nt" or pid <= 4:
        return ""
    from . import winapi as w
    h = w.OpenProcess(w.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_ulong(1024)
        return buf.value if w.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        w.CloseHandle(h)
