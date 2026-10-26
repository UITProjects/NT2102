"""schema.py - what each event type's payload looks like, asked from Windows (TDH) once and cached.

TDH ("Trace Data Helper", tdh.dll) reads the provider's manifest and returns a TRACE_EVENT_INFO for an event:
provider name, task name ('Create'), opcode name ('Info'), and the list of payload fields with their in-types.
That call is slow-ish, but all events of one type (provider, event id, version) share it, so it runs once per
type (a few dozen types in total) and is cached as an EventPlan.

Fast path: every top-level field is a simple scalar/string -> EventPlan.props (decode.Prop list), and each event
is decoded in pure Python from its bytes. Slow path (structs, arrays, counted strings): ask TDH for each field's
bytes per event (TdhGetPropertySize + TdhGetProperty). None of our 3 providers is expected to need it; the
counter `slow_events` in collector-status.json shows it if they do.

TRACE_EVENT_INFO buffer layout ([n B] = bytes):
    [112 B] header (offsets of the name strings, PropertyCount, TopLevelPropertyCount, ...)
    [24 B x PropertyCount] EVENT_PROPERTY_INFO (Flags, NameOffset, InType, OutType, Count, Length)
    [...] UTF-16 strings; every *Offset above is a byte offset from the start of the buffer
"""
from __future__ import annotations

import ctypes
import uuid
from dataclasses import dataclass, field

from . import decode as dc
from . import winapi as w
from .providers import NETWORK
from .record import event_name, opcode_label

IPV4_NAMES, PORT_NAMES = {"daddr", "saddr"}, {"dport", "sport"}


@dataclass
class EventPlan:
    provider_guid: str
    provider_name: str
    event_name: str
    opcode: int
    opcode_name: str
    props: list | None                      # decode.Prop list (fast path) or None (slow path)
    slow: list = field(default_factory=list)  # [(Prop, PROPERTY_DATA_DESCRIPTOR)] for the slow path
    buf: object = None                      # keeps the TRACE_EVENT_INFO buffer alive (names point into it)

    def decode(self, rec_ptr, data: bytes, ptr_size: int) -> tuple[dict[str, str], bool]:
        """-> (fields, used_slow_path)."""
        if self.props is not None:
            return dc.decode(self.props, data, ptr_size), False
        out = {}
        for p, pdd in self.slow:
            size = ctypes.c_ulong(0)
            if w.TdhGetPropertySize(rec_ptr, 0, None, 1, ctypes.byref(pdd), ctypes.byref(size)) != 0:
                continue
            raw = ctypes.create_string_buffer(max(size.value, 1))
            if w.TdhGetProperty(rec_ptr, 0, None, 1, ctypes.byref(pdd), size.value, raw) != 0:
                continue
            try:
                out[p.name] = dc.decode_value(p, raw.raw[:size.value], ptr_size)
            except (dc.DecodeError, ValueError, IndexError):
                out[p.name] = "0x" + raw.raw[:size.value].hex()
        return out, True


def _fmt(provider_name: str, name: str, intype: int, outtype: int) -> str | None:
    if outtype in (dc.OUT_HEXINT8, dc.OUT_HEXINT16, dc.OUT_HEXINT32, dc.OUT_HEXINT64):
        return "hex"
    net = provider_name == NETWORK
    if outtype == dc.OUT_IPV4 or (net and name in IPV4_NAMES and intype in (dc.IN_UINT32, dc.IN_INT32)):
        return "ipv4"
    if outtype == dc.OUT_IPV6 or (net and name in IPV4_NAMES and intype == dc.IN_BINARY):
        return "ipv6"
    if outtype == dc.OUT_PORT or (net and name in PORT_NAMES and intype in (dc.IN_UINT16, dc.IN_INT16)):
        return "port"
    return None


class Schemas:
    def __init__(self, provider_names: dict[bytes, str]):
        self.cache: dict[tuple, EventPlan] = {}
        self.provider_names = provider_names      # guid_le -> name (fallback when TDH has no schema)
        self.no_schema = 0

    def get(self, rec_ptr) -> EventPlan:
        h = rec_ptr.contents.EventHeader
        d = h.EventDescriptor
        key = (bytes(h.ProviderId), d.Id, d.Version, d.Opcode, d.Task)
        plan = self.cache.get(key)
        if plan is None:
            plan = self.cache[key] = self._build(rec_ptr)
        return plan

    def _build(self, rec_ptr) -> EventPlan:
        h = rec_ptr.contents.EventHeader
        d = h.EventDescriptor
        guid_le = bytes(h.ProviderId)
        gstr = str(uuid.UUID(bytes_le=guid_le))
        size = ctypes.c_ulong(0)
        rc = w.TdhGetEventInformation(rec_ptr, 0, None, None, ctypes.byref(size))
        if rc == w.ERROR_INSUFFICIENT_BUFFER:
            buf = ctypes.create_string_buffer(size.value)
            rc = w.TdhGetEventInformation(rec_ptr, 0, None, buf, ctypes.byref(size))
        if rc != 0:                               # no manifest for this event: header only, empty payload
            self.no_schema += 1
            return EventPlan(gstr, self.provider_names.get(guid_le, gstr), f"EventID({d.Id})", d.Opcode,
                             opcode_label("", d.Opcode), [])
        base = ctypes.addressof(buf)
        tei = w.TRACE_EVENT_INFO.from_buffer(buf)

        def s(off: int) -> str:
            return ctypes.wstring_at(base + off).strip() if off else ""

        pname = s(tei.ProviderNameOffset) or self.provider_names.get(guid_le, gstr)
        task, opname = s(tei.TaskNameOffset), s(tei.OpcodeNameOffset)
        arr = base + ctypes.sizeof(w.TRACE_EVENT_INFO)
        props, slow, fast = [], [], True
        for i in range(tei.TopLevelPropertyCount):
            epi = w.EVENT_PROPERTY_INFO.from_address(arr + i * ctypes.sizeof(w.EVENT_PROPERTY_INFO))
            name = s(epi.NameOffset)
            fl = epi.Flags
            p = dc.Prop(name, epi.InType, _fmt(pname, name, epi.InType, epi.OutType))
            pdd = w.PROPERTY_DATA_DESCRIPTOR(base + epi.NameOffset, 0xFFFFFFFF, 0)
            if fl & w.PropertyStruct:
                fast = False                       # struct: slow path, field kept as one hex blob
                slow.append((dc.Prop(name, dc.IN_BINARY), pdd))
                continue
            slow.append((p, pdd))
            if fl & w.PropertyParamCount or epi.Count > 1 or epi.InType not in dc.SUPPORTED:
                fast = False
            elif epi.InType in (dc.IN_UNICODESTRING, dc.IN_ANSISTRING) and (fl & w.PropertyParamLength or epi.Length):
                fast = False                       # counted strings: let TDH cut them
            elif epi.InType == dc.IN_BINARY:
                if fl & w.PropertyParamLength:
                    p = dc.Prop(name, epi.InType, p.fmt, length_index=epi.Length)
                elif epi.Length:
                    p = dc.Prop(name, epi.InType, p.fmt, length=epi.Length)
                else:
                    fast = False
            props.append(p)
        return EventPlan(gstr, pname, event_name(task, opname, d.Opcode, d.Id), d.Opcode,
                         opcode_label(opname, d.Opcode), props if fast else None, slow, buf)
