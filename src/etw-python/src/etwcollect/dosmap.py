"""dosmap.py - NT device names <-> drive letters ('\\device\\harddiskvolume4' <-> 'c:').

Kernel-File and Kernel-Process report paths in NT device form (\\Device\\HarddiskVolume4\\Windows\\...). The
converter on the trainer needs this machine's mapping to turn them into C:\\Windows\\..., so every run carries
it: dosmap.meta in the run folder and one job="etw-meta" line in Loki (same JSON as
`etwship.py --print-dos-map`, which this replaces in etwctl).

    {"\\\\device\\\\harddiskvolume4": "c:"}        (JSON-escaped backslashes)
"""
from __future__ import annotations

import ctypes
import json
import os


def dos_device_map() -> dict[str, str]:
    """QueryDosDeviceW('C:') -> '\\Device\\HarddiskVolume4' for every drive letter; longest device first so a
    prefix match never picks \\device\\harddiskvolume1 for \\device\\harddiskvolume12. Empty off Windows."""
    m: dict[str, str] = {}
    if os.name != "nt":
        return m
    from . import winapi as w
    buf = ctypes.create_unicode_buffer(1024)
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if w.QueryDosDeviceW(c + ":", buf, 1024):
            m[buf.value.lower()] = c.lower() + ":"
    return dict(sorted(m.items(), key=lambda kv: -len(kv[0])))


def to_device(path: str, dmap: dict[str, str]) -> str:
    """'C:\\lab-out\\etw\\raw' -> '\\device\\harddiskvolume4\\lab-out\\etw\\raw' (lower case), the form Kernel-File
    reports. Unchanged when the drive is not in the map."""
    p = os.path.normpath(path).lower() if os.name == "nt" else path.lower()
    for dev, drive in dmap.items():
        if p.startswith(drive):
            return dev + p[len(drive):]
    return p


def write_meta(path: str, dmap: dict[str, str]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(dmap, separators=(",", ":")) + "\n")
