"""record.py - one decoded event -> one SilkETW-format JSON line.

The converter (src/pipeline/etw2nodlink.py), the Loki labels and every notebook read SilkETW's line shape, so
we keep its top-level keys and their order:

    {"ProviderGuid":"edd08927-...","YaraMatch":[],"ProviderName":"Microsoft-Windows-Kernel-File",
     "EventName":"Create","Opcode":0,"OpcodeName":"Info","TimeStamp":"2026-10-25T07:30:45.8725831+00:00",
     "ThreadID":4888,"ProcessID":420,"ProcessName":"explorer","PointerSize":8,"EventDataLength":188,
     "XmlEventData":{"FileObject":"0xffffcd0213013800","FileName":"\\Device\\HarddiskVolume4\\...",...}}

Differences from SilkETW (DEVIATIONS D10): XmlEventData holds only the payload fields (no FormattedMessage,
no TraceEvent copies of the header such as MSec/PName/TID/PID), values are plain (see decode.py), and the
timestamp is UTC (+00:00) instead of the VM's local offset. Every consumer converts it to epoch ms anyway.
"""
from __future__ import annotations

import json

from .decode import filetime_iso


def ident(s: str | None) -> str:
    """TraceEvent's names drop the spaces of the manifest strings: 'Data sent.' -> 'Datasent.'."""
    return "".join(s.split()) if s else ""


def event_name(task: str, opcode_name: str, opcode: int, event_id: int) -> str:
    """TraceEvent's EventName rule, which the converter's classify() was written against:
        task 'Create',  opcode 0             -> 'Create'
        task 'ProcessStart', opcode 1 'Start' -> 'ProcessStart/Start'
        task 'KERNEL_NETWORK_TASK_TCPIP', opcode 10 'Data sent.' -> 'KERNEL_NETWORK_TASK_TCPIP/Datasent.'"""
    task, op = ident(task), ident(opcode_name)
    if not task:
        return f"EventID({event_id})"
    if opcode == 0:
        return task
    return f"{task}/{op or opcode}"


def opcode_label(opcode_name: str, opcode: int) -> str:
    return ident(opcode_name) or ("Info" if opcode == 0 else str(opcode))


def build(meta, header: dict, props: dict[str, str]) -> dict:
    """meta: schema.EventPlan (provider_guid, provider_name, event_name, opcode, opcode_name).
    header: {"ts": FILETIME int, "tid": int, "pid": int, "pname": str, "ptr": 4|8, "len": UserDataLength}."""
    return {
        "ProviderGuid": meta.provider_guid,
        "YaraMatch": [],
        "ProviderName": meta.provider_name,
        "EventName": meta.event_name,
        "Opcode": meta.opcode,
        "OpcodeName": meta.opcode_name,
        "TimeStamp": filetime_iso(header["ts"]),
        "ThreadID": header["tid"],
        "ProcessID": header["pid"],
        "ProcessName": header["pname"],
        "PointerSize": header["ptr"],
        "EventDataLength": header["len"],
        "XmlEventData": props,
    }


def line(rec: dict) -> bytes:
    """Compact JSON (no spaces, like SilkETW), UTF-8, one line + '\\n'."""
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
