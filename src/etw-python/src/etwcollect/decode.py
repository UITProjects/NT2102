"""decode.py - raw ETW payload bytes (EVENT_RECORD.UserData) -> {property name: string}. Pure Python.

An ETW event carries its fields as one packed byte string, in the order the provider's manifest declares
them. The manifest (read once per event type through TDH, schema.py) gives each field a name and an "in-type"
(how it is stored: UINT32, POINTER, UNICODESTRING, ...). schema.py turns that into a list of Prop; this module
walks the bytes with that list. Nothing here calls Windows, so the selftest runs it on Linux.

    data: bytes = b'\\x00\\x38\\x01\\x13\\x02\\xcd\\xff\\xff' + 'C:\\x.txt\\0' (UTF-16) + ...
    props: list[Prop] = [Prop("FileObject", IN_POINTER), Prop("FileName", IN_UNICODESTRING), ...]
    decode(props, data, ptr_size=8) -> {"FileObject": "0xffffcd0213013800", "FileName": "C:\\x.txt", ...}

Values are written plainly (D10): decimal ints without thousands separators, pointers and hex ints as 0x..,
IPv4 dotted, ports in host order. SilkETW (via TraceEvent) wrote "4,888" and signed ints for addresses.
"""
from __future__ import annotations

import ipaddress
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

# TDH_INTYPE (tdh.h): how a field is stored in the payload.
IN_NULL, IN_UNICODESTRING, IN_ANSISTRING = 0, 1, 2
IN_INT8, IN_UINT8, IN_INT16, IN_UINT16, IN_INT32, IN_UINT32, IN_INT64, IN_UINT64 = range(3, 11)
IN_FLOAT, IN_DOUBLE, IN_BOOLEAN, IN_BINARY, IN_GUID, IN_POINTER = range(11, 17)
IN_FILETIME, IN_SYSTEMTIME, IN_SID, IN_HEXINT32, IN_HEXINT64 = range(17, 22)
IN_UNICODECHAR, IN_ANSICHAR, IN_SIZET = 22, 23, 24

# TDH_OUTTYPE values we care about: how the manifest wants a field shown.
OUT_HEXINT8, OUT_HEXINT16, OUT_HEXINT32, OUT_HEXINT64 = 16, 17, 18, 19
OUT_PORT, OUT_IPV4, OUT_IPV6 = 22, 23, 24

# in-type -> (struct format, byte size). "<" = little-endian, the byte order of every x86/x64 Windows field.
FIXED: dict[int, tuple[str, int]] = {
    IN_INT8: ("<b", 1), IN_UINT8: ("<B", 1), IN_ANSICHAR: ("<B", 1),
    IN_INT16: ("<h", 2), IN_UINT16: ("<H", 2), IN_UNICODECHAR: ("<H", 2),
    IN_INT32: ("<i", 4), IN_UINT32: ("<I", 4), IN_HEXINT32: ("<I", 4), IN_BOOLEAN: ("<i", 4),
    IN_INT64: ("<q", 8), IN_UINT64: ("<Q", 8), IN_HEXINT64: ("<Q", 8),
    IN_FLOAT: ("<f", 4), IN_DOUBLE: ("<d", 8), IN_FILETIME: ("<Q", 8),
}
VARIABLE = {IN_UNICODESTRING, IN_ANSISTRING, IN_BINARY, IN_SID, IN_GUID, IN_SYSTEMTIME, IN_POINTER, IN_SIZET}
SUPPORTED = set(FIXED) | VARIABLE

FILETIME_UNIX = 116_444_736_000_000_000     # 1601-01-01 -> 1970-01-01 in 100 ns ticks


class DecodeError(ValueError):
    pass


@dataclass(frozen=True)
class Prop:
    """One top-level payload field, as schema.py read it from the manifest."""
    name: str
    intype: int
    fmt: str | None = None          # None | "hex" | "port" | "ipv4" | "ipv6"
    length: int | None = None       # fixed byte length (BINARY only)
    length_index: int | None = None # BINARY: index of the earlier field that holds its byte length


def filetime_iso(ft: int) -> str:
    """FILETIME (100 ns ticks since 1601, UTC) -> '2026-10-25T07:30:47.8891166+00:00' (SilkETW's 7 digits).
    ft = 134_373_870_478_891_166  ->  '2026-10-25T07:30:47.8891166+00:00'
    ft = 0                        ->  '0' (a 'not set' FILETIME field)"""
    if ft <= 0:
        return "0"
    sec, frac = divmod(ft - FILETIME_UNIX, 10_000_000)       # whole seconds since 1970, 100 ns remainder
    dt = datetime.fromtimestamp(sec, tz=timezone.utc)
    return f"{dt:%Y-%m-%dT%H:%M:%S}.{frac:07d}+00:00"


def filetime_ns(ft: int) -> int:
    """FILETIME -> Unix epoch nanoseconds (Loki entry timestamps)."""
    return (ft - FILETIME_UNIX) * 100


def _wcs_end(data: bytes, off: int) -> int:
    """Byte offset of the UTF-16 NUL terminator at or after off (len(data) when unterminated). The NUL must
    start on an even distance from off: b'A\\x00\\x00\\x01' has a 0x0000 pair at 1, but that is not a char."""
    i = off
    while True:
        j = data.find(b"\x00\x00", i)
        if j < 0:
            return len(data)
        if (j - off) % 2 == 0:
            return j
        i = j + 1


def _sid(b: bytes) -> str:
    """Binary SID -> 'S-1-5-18'. Layout: revision u8, sub-authority count u8, authority 6 bytes big-endian,
    then count x u32 little-endian."""
    rev, n = b[0], b[1]
    auth = int.from_bytes(b[2:8], "big")
    subs = struct.unpack_from("<%dI" % n, b, 8)
    return "S-%d-%d" % (rev, auth) + "".join("-%d" % s for s in subs)


def _systemtime(b: bytes) -> str:
    y, mo, _dow, d, h, mi, s, ms = struct.unpack("<8H", b)
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}.{ms:03d}"


def format_int(v: int, fmt: str | None, size: int) -> str:
    if fmt == "hex":
        return "0x%x" % (v & ((1 << (8 * size)) - 1))
    return str(v)


def read_one(p: Prop, data: bytes, off: int, ptr_size: int, nums: list) -> tuple[str, int, int | None]:
    """Decode prop p at data[off:]. Returns (text, next offset, int value or None). nums = int values of the
    props decoded so far (a BINARY field's length lives in an earlier field)."""
    t = p.intype
    if t in FIXED:
        f, n = FIXED[t]
        if off + n > len(data):
            raise DecodeError(f"{p.name}: needs {n} bytes at {off}, payload has {len(data)}")
        if p.fmt == "port" and n == 2:                      # stored in network byte order (big-endian)
            v = struct.unpack_from(">H", data, off)[0]
            return str(v), off + n, v
        if p.fmt == "ipv4" and n == 4:                      # in_addr: the 4 bytes ARE a.b.c.d in order
            return ".".join(str(x) for x in data[off:off + 4]), off + n, None
        v = struct.unpack_from(f, data, off)[0]
        if t == IN_BOOLEAN:
            return ("True" if v else "False"), off + n, v
        if t == IN_FILETIME:
            return filetime_iso(v), off + n, v
        if t in (IN_FLOAT, IN_DOUBLE):
            return repr(v), off + n, None
        hexed = "hex" if t in (IN_HEXINT32, IN_HEXINT64) else p.fmt
        return format_int(v, hexed, n), off + n, v
    if t in (IN_POINTER, IN_SIZET):
        if off + ptr_size > len(data):
            raise DecodeError(f"{p.name}: pointer at {off} past payload end {len(data)}")
        v = int.from_bytes(data[off:off + ptr_size], "little")
        return "0x%x" % v, off + ptr_size, v
    if t == IN_UNICODESTRING:
        end = _wcs_end(data, off)
        s = data[off:end].decode("utf-16-le", errors="replace")
        return s, min(end + 2, len(data)), None
    if t == IN_ANSISTRING:
        end = data.find(b"\x00", off)
        end = len(data) if end < 0 else end
        return data[off:end].decode("cp1252", errors="replace"), min(end + 1, len(data)), None
    if t == IN_GUID:
        if off + 16 > len(data):
            raise DecodeError(f"{p.name}: GUID past payload end")
        return str(uuid.UUID(bytes_le=data[off:off + 16])), off + 16, None
    if t == IN_SYSTEMTIME:
        if off + 16 > len(data):
            raise DecodeError(f"{p.name}: SYSTEMTIME past payload end")
        return _systemtime(data[off:off + 16]), off + 16, None
    if t == IN_SID:
        if off + 8 > len(data):
            raise DecodeError(f"{p.name}: SID header past payload end")
        n = 8 + 4 * data[off + 1]
        return _sid(data[off:off + n]), off + n, None
    if t == IN_BINARY:
        n = p.length if p.length is not None else (nums[p.length_index] if p.length_index is not None else None)
        if n is None:
            raise DecodeError(f"{p.name}: BINARY without a length")
        raw = data[off:off + n]
        if p.fmt == "ipv6" and n == 16:
            return str(ipaddress.IPv6Address(raw)), off + n, None
        if p.fmt == "ipv4" and n == 4:
            return ".".join(str(x) for x in raw), off + n, None
        return "0x" + raw.hex(), off + n, None
    raise DecodeError(f"{p.name}: unsupported in-type {t}")


def decode(props: list[Prop], data: bytes, ptr_size: int = 8) -> dict[str, str]:
    """Walk data with props. A payload that ends early (an older event version without the trailing
    fields) stops cleanly; a field cut in half raises DecodeError."""
    out: dict[str, str] = {}
    nums: list = []
    off = 0
    for p in props:
        if off >= len(data):
            break
        text, off, num = read_one(p, data, off, ptr_size, nums)
        out[p.name] = text
        nums.append(num)
    return out


def decode_value(p: Prop, raw: bytes, ptr_size: int = 8) -> str:
    """One field whose exact bytes TDH already cut out (the slow path in schema.py)."""
    if p.intype == IN_BINARY:
        p = Prop(p.name, p.intype, p.fmt, len(raw))
    return read_one(p, raw, 0, ptr_size, [])[0]
