"""selftest.py - `etwcollect selftest` (any OS) and `etwcollect selftest --live` (Windows, elevated).

A selftest proves "it runs", never "it captures everything" (CLAUDE.md §6). What each part checks:
  offline  decode of a synthetic payload (every in-type we use, IPv4/port, length-prefixed binary, early end);
           EventName rule + timestamp; exclusion rules (a)-(c); whole-line batching; the Loki shipper against a
           fake Loki (labels, metadata, event-time timestamps, resume from offsets, retry, refusal, meta line,
           ledger); our lines through the real converter (etw2nodlink.py) when it can be found.
  --live   struct sizes vs the C headers; a real 15 s ETW session: a child process + a canary file must show
           up, nothing from our own PID may be written, the converter turns the capture into edges.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import decode as dc
from . import record
from .filters import Excluder, image_base
from .loki import LokiClient, Shipper, push_ledger, ts_ns
from .writer import LineWriter

KP, KF, KN = ("Microsoft-Windows-Kernel-" + x for x in ("Process", "File", "Network"))


def check(cond, what: str) -> None:
    if not cond:
        raise AssertionError(what)
    print(f"  ok  {what}")


# --------------------------------------------------------------------------- fake Loki
class FakeLoki:
    """POST /loki/api/v1/push only. fail_next = n -> the next n pushes answer `code`."""

    def __init__(self):
        self.entries, self.fail_next, self.code = [], 0, 500
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if fake.fail_next > 0:
                    fake.fail_next -= 1
                    self.send_response(fake.code)
                    self.send_header("Content-Length", "4")
                    self.end_headers()
                    self.wfile.write(b"nope")
                    return
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                for s in json.loads(raw)["streams"]:
                    for v in s["values"]:
                        fake.entries.append((int(v[0]), s["stream"], v[1], v[2] if len(v) > 2 else {}))
                self.send_response(204)
                self.end_headers()

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


class _Plan:                                                  # stands in for schema.EventPlan
    def __init__(self, prov, name, opcode=0, opname="Info"):
        self.provider_guid = {KP: "22fb2cd6-0e7b-422b-a0c7-2fad1fd0e716", KF: "edd08927-9cc4-4e65-b970-c2560fb5c289",
                              KN: "7dd42a49-5329-4832-8dfd-43d979153a88"}[prov]
        self.provider_name, self.event_name, self.opcode, self.opcode_name = prov, name, opcode, opname


def _ft(sec_from_base: float) -> int:
    """FILETIME for 2026-10-26T08:00:00Z + sec."""
    return dc.FILETIME_UNIX + int((1_793_001_600 + sec_from_base) * 10_000_000)


def _line(prov, name, pid, pname, sec, props, opcode=0, opname="Info") -> bytes:
    hdr = {"ts": _ft(sec), "tid": 1, "pid": pid, "pname": pname, "ptr": 8, "len": 0}
    return record.line(record.build(_Plan(prov, name, opcode, opname), hdr, props))


# --------------------------------------------------------------------------- offline parts
def t_decode():
    print("decode")
    sid = bytes([1, 1, 0, 0, 0, 0, 0, 5]) + struct.pack("<I", 18)          # S-1-5-18
    g = uuid.UUID("edd08927-9cc4-4e65-b970-c2560fb5c289")
    data = (struct.pack("<Q", 0xffffcd0213013800) + "\\Device\\X\\a.txt\0".encode("utf-16-le")
            + struct.pack("<I", 4096) + struct.pack("<I", 0x10) + bytes([23, 220, 64, 154]) + struct.pack(">H", 443)
            + struct.pack("<I", 3) + b"\x01\x02\x03" + sid + g.bytes_le + struct.pack("<Q", _ft(0))
            + struct.pack("<i", 1) + struct.pack("<h", -5))
    P = dc.Prop
    props = [P("FileObject", dc.IN_POINTER), P("FileName", dc.IN_UNICODESTRING), P("IOSize", dc.IN_UINT32),
             P("Flags", dc.IN_HEXINT32), P("daddr", dc.IN_UINT32, "ipv4"), P("dport", dc.IN_UINT16, "port"),
             P("n", dc.IN_UINT32), P("blob", dc.IN_BINARY, length_index=6), P("Sid", dc.IN_SID),
             P("Guid", dc.IN_GUID), P("When", dc.IN_FILETIME), P("Ok", dc.IN_BOOLEAN), P("Delta", dc.IN_INT16)]
    d = dc.decode(props, data, 8)
    check(d["FileObject"] == "0xffffcd0213013800" and d["FileName"] == "\\Device\\X\\a.txt", "pointer + UTF-16 string")
    check(d["IOSize"] == "4096" and d["Flags"] == "0x10", "uint32 plain, hexint32 as 0x..")
    check(d["daddr"] == "23.220.64.154" and d["dport"] == "443", "IPv4 dotted, port in host order")
    check(d["blob"] == "0x010203" and d["Sid"] == "S-1-5-18" and d["Guid"] == str(g), "binary by length field, SID, GUID")
    check(d["When"] == "2026-10-26T08:00:00.0000000+00:00" and d["Ok"] == "True" and d["Delta"] == "-5",
          "FILETIME, BOOLEAN, signed int16")
    odd = b"A\x00\x00\x01\x00\x00Z"                          # 'A', U+0100, NUL: the 00 00 at byte 1 is not a NUL char
    check(dc.read_one(P("s", dc.IN_UNICODESTRING), odd, 0, 8, [])[0] == "A\u0100", "UTF-16 NUL found on a char boundary")
    short = dc.decode(props, data[:8 + 30], 8)                # ends right after FileName: trailing fields absent
    check(list(short) == ["FileObject", "FileName"], "payload that ends early stops cleanly")
    try:
        dc.decode([P("x", dc.IN_UINT64)], b"\x01\x02\x03", 8)
        check(False, "half a field raises")
    except dc.DecodeError:
        check(True, "half a field raises DecodeError")


def t_record():
    print("record")
    check(record.event_name("Create", "Info", 0, 12) == "Create", "opcode 0 -> task name")
    check(record.event_name("ProcessStart", "Start", 1, 1) == "ProcessStart/Start", "task/opcode")
    check(record.event_name("KERNEL_NETWORK_TASK_TCPIP", "Data sent.", 10, 10) == "KERNEL_NETWORK_TASK_TCPIP/Datasent.",
          "spaces dropped like TraceEvent")
    check(record.event_name("", "", 0, 77) == "EventID(77)", "no task -> EventID(n)")
    ev = json.loads(_line(KF, "Create", 420, "explorer", 1.5, {"FileObject": "0xaa"}))
    check(list(ev) == ["ProviderGuid", "YaraMatch", "ProviderName", "EventName", "Opcode", "OpcodeName", "TimeStamp",
                       "ThreadID", "ProcessID", "ProcessName", "PointerSize", "EventDataLength", "XmlEventData"],
          "SilkETW key order")
    check(ev["TimeStamp"] == "2026-10-26T08:00:01.5000000+00:00" and ts_ns(ev["TimeStamp"]) == (1_793_001_601_500_000_000),
          "timestamp 7 digits, UTC, round-trips to ns")
    check(ts_ns("2026-10-26T15:00:00.1234567+07:00") == 1_793_001_600_123_456_700, "+07:00 offset handled")
    check(image_base("\\Device\\HarddiskVolume4\\Windows\\System32\\msedge.exe") == "msedge", "image name without .exe")


def t_filters():
    print("filters")
    own = r"\device\harddiskvolume4\lab-out\etw\raw" + "\\"
    ex = Excluder({999}, ("alloy",), [own, r"\device\harddiskvolume4\programdata\etwlab\collector-status.json"])
    check(ex.header("kproc", 999) and not ex.header("kproc", 4), "(a) own PID dropped before decode")
    ours = r"\Device\HarddiskVolume4\lab-out\etw\raw\test\live-1\kfile.json"
    check(ex.kfile("Create", 999, {"FileObject": "0xA1", "FileName": ours}), "(b) our own Create dropped, handle learned")
    check(ex.kfile("Write", 999, {"FileObject": "0xa1", "FileKey": "0xK1"}), "(b) our Write dropped, file key learned")
    check(ex.kfile("Write", 4, {"FileObject": "0xB2", "FileKey": "0xk1"}), "(b) System lazy write of our file dropped (key)")
    check(not ex.kfile("Create", 77, {"FileObject": "0xa1", "FileName": r"\Device\HarddiskVolume4\tmp\x.txt"}),
          "a reused file object for another file is kept ...")
    check(not ex.kfile("Read", 77, {"FileObject": "0xa1", "FileKey": "0xk9"}), "... and forgotten")
    check(ex.kfile("Read", 5, {"FileObject": "0xc3",
                               "FileName": r"\device\harddiskvolume4\programdata\etwlab\collector-status.json"}) is False
          and ex.kfile("Create", 5, {"FileObject": "0xc3",
                                     "FileName": r"\device\harddiskvolume4\programdata\etwlab\collector-status.json"}),
          "exact own file matched, its folder is not")
    ex.note_process(420, "alloy-windows-amd64")
    check(ex.knet({"PID": "420"}) and ex.knet({"PID": "999"}) and not ex.knet({"PID": "7172"}), "(c) payload PID decides")
    ex.note_process(420, "notepad")
    check(not ex.knet({"PID": "420"}), "a reused PID of another image is no longer excluded")
    check(ex.counts["kfile.own_file"] == 4 and ex.counts["knet.net_pid"] == 2, "counters")


def t_writer(tmp):
    print("writer")
    w = LineWriter(os.path.join(tmp, "w"), ["kfile"])
    w.add("kfile", b'{"a":1}\n')
    w.add("kfile", b'{"a":2}\n')
    check(os.path.getsize(w.paths["kfile"]) == 0, "nothing on disk before the 1 s flush")
    check(w.flush() == 2 and open(w.paths["kfile"], "rb").read() == b'{"a":1}\n{"a":2}\n', "one write, whole lines")
    w.close()


def t_shipper(tmp):
    print("shipper (fake Loki)")
    fake = FakeLoki()
    try:
        run = os.path.join(tmp, "raw", "test", "live-1")
        os.makedirs(run)
        p = os.path.join(run, "kfile.json")
        a = _line(KF, "Create", 420, "explorer", 1, {"FileObject": "0xaa"})
        b = _line(KF, "Write", 420, "explorer", 2, {"FileObject": "0xaa"})
        c = _line(KF, "Read", 7, "N/A", 3, {"FileObject": "0xbb"})
        with open(p, "wb") as fh:
            fh.write(a + b + c[:20])                         # third line half written
        pos = os.path.join(tmp, "pos.json")
        sh = Shipper(LokiClient(fake.url), pos, "ws-etw", log=lambda *_: None)
        sh.track(p)
        check(sh.step() == 2 and len(fake.entries) == 2, "whole lines shipped, the half line waits")
        t, lab, line, meta = fake.entries[0]
        check(lab == {"job": "silketw", "host": "ws-etw", "split": "test", "run": "live-1", "provider": "kfile"},
              "labels = Alloy's (job, host, split, run, provider)")
        check(meta == {"event": "Create", "pid": "420", "process": "explorer"} and line == a.decode().rstrip("\n"),
              "structured metadata + unchanged line")
        check(t == 1_793_001_601 * 1_000_000_000, "entry timestamp = event time")
        with open(p, "ab") as fh:
            fh.write(c[20:])
        fake.fail_next, fake.code = 1, 503
        check(sh.step() == 0 and len(fake.entries) == 2, "Loki 503: offset kept")
        sh.backoff_until = 0
        check(sh.step() == 1 and len(fake.entries) == 3, "retried after backoff")
        sh2 = Shipper(LokiClient(fake.url), pos, "ws-etw", log=lambda *_: None)
        check(sh2.step() == 0 and len(fake.entries) == 3, "restart resumes from the saved offset: no duplicates")
        check(sh2.push_meta("test", "live-1", {"\\device\\harddiskvolume4": "c:"}) and
              sh2.push_meta("test", "live-1", {}) and len(fake.entries) == 4 and fake.entries[3][1]["job"] == "etw-meta",
              "dos-map line pushed once per run")
        with open(p, "ab") as fh:
            fh.write(b"not json\n")
        fake.fail_next, fake.code = 1, 400
        check(sh2.step() == 0 and sh2.rejected == 1 and not any(sh2.backlog().values()), "Loki 400: batch skipped, no stall")
        sh2.close_run(run)
        check(sh2.drain(2) and sh2.step() == 0 and not sh2.state["files"], "closed run forgotten once shipped")
        led = os.path.join(tmp, "labels")
        os.makedirs(led)
        with open(os.path.join(led, "ledger.csv"), "w", encoding="utf-8") as fh:
            fh.write('"utc","scenario","action","target"\n"2026-10-26T08:00:05.0000000Z","notes","edit","x.txt"\n')
        n0 = len(fake.entries)
        check(push_ledger(LokiClient(fake.url), "ws-etw", "20261026T080000Z", led) == 2
              and fake.entries[n0 + 1][0] == 1_793_001_605 * 1_000_000_000
              and fake.entries[n0][1] == {"job": "workload-ledger", "host": "ws-etw", "exp": "20261026T080000Z"},
              "ledger rows pushed with their utc time")
    finally:
        fake.close()


def _find_converter() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.environ.get("ETW2NODLINK"),
              os.path.join(here, "..", "..", "..", "pipeline", "etw2nodlink.py"),   # repo: src/pipeline
              r"C:\ProgramData\etwlab\etw2nodlink.py"):                             # the VM copy (Ansible)
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


def _load_converter(path):
    spec = importlib.util.spec_from_file_location("etw2nodlink", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def t_converter(tmp):
    print("converter round-trip")
    path = _find_converter()
    if not path:
        print("  skip  etw2nodlink.py not found (set ETW2NODLINK=path)")
        return
    conv = _load_converter(path)
    run = os.path.join(tmp, "conv", "raw", "train", "r1")
    os.makedirs(run)
    dev = "\\Device\\HarddiskVolume2"
    lines = {
        "kproc.json": [_line(KP, "ProcessStart/Start", 100, "explorer", 0, {"ProcessID": "4321", "ParentProcessID": "100",
                             "ImageName": dev + "\\Windows\\System32\\cmd.exe"}, 1, "Start"),
                       _line(KP, "ImageLoad", 4321, "cmd", 0.1, {"ProcessID": "4321",
                             "ImageName": dev + "\\Windows\\System32\\ntdll.dll"})],
        "kfile.json": [_line(KF, "Create", 4321, "cmd", 1, {"FileObject": "0xaa", "FileName": dev + "\\tmp\\x.txt"}),
                       _line(KF, "Write", 4321, "cmd", 1.5, {"FileObject": "0xaa", "FileKey": "0xbb", "IOSize": "4096"})],
        "knet.json": [_line(KN, "KERNEL_NETWORK_TASK_TCPIP/Datasent.", 0, "Idle", 3, {"PID": "4321",
                             "daddr": "93.184.216.34", "dport": "443", "size": "100"}, 10, "Datasent.")],
    }
    for fn, ls in lines.items():
        with open(os.path.join(run, fn), "wb") as fh:
            fh.write(b"".join(ls))
    s = conv.convert(os.path.join(tmp, "conv", "raw"), "train", "SelfTest", os.path.join(tmp, "conv", "out"),
                     {"\\device\\harddiskvolume2": "c:"})
    check(s["edges"] == {"Start": 1, "Load": 1, "Write": 1, "Send": 1}, f"converter edges {s['edges']}")
    with open(os.path.join(tmp, "conv", "out", "SelfTest_train_edges.csv"), encoding="utf-8") as fh:
        send = [r for r in fh if ",Send," in r][0]
    check(send.startswith("p4321_"), "Send edge hangs off the payload PID's process (not Idle)")


def offline() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            t_decode()
            t_record()
            t_filters()
            t_writer(tmp)
            t_shipper(tmp)
            t_converter(tmp)
        except AssertionError as e:
            print(f"  FAIL {e}")
            print("[SELFTEST FAILED]")
            return 1
    print("[SELFTEST OK] decode, record, filters, writer, shipper, converter round-trip")
    return 0


# --------------------------------------------------------------------------- live (Windows)
def live() -> int:
    if os.name != "nt":
        print("--live needs Windows (and an elevated shell)")
        return 2
    import ctypes
    from . import winapi as w
    from .collector import Collector, Config
    from .providers import Provider
    from .session import RealtimeSession
    rc = offline()
    if rc:
        return rc
    print("live")
    try:
        sizes = {"EVENT_TRACE_PROPERTIES": (w.EVENT_TRACE_PROPERTIES, 120), "EVENT_RECORD": (w.EVENT_RECORD, 112),
                 "EVENT_TRACE_LOGFILEW": (w.EVENT_TRACE_LOGFILEW, 448), "TRACE_EVENT_INFO": (w.TRACE_EVENT_INFO, 112),
                 "EVENT_PROPERTY_INFO": (w.EVENT_PROPERTY_INFO, 24)}
        for n, (t, size) in sizes.items():
            check(ctypes.sizeof(t) == size, f"sizeof({n}) == {size} (x64 C headers)")
        root = tempfile.mkdtemp(prefix="etwcollect-")
        out = os.path.join(root, "raw", "test", "selftest-live")
        provs = [Provider("kproc", KP, "0x50"), Provider("kfile", KF, "0x380"), Provider("knet", KN, "0x30")]
        cfg = Config(out_dir=out, split="test", run="selftest-live", providers=provs, raw_root=os.path.join(root, "raw"),
                     status_path=os.path.join(root, "status.json"), positions_path=os.path.join(root, "pos.json"),
                     push=False, session="etwlab-selftest")
        col = Collector(cfg, log=lambda m: print("  " + m))
        th = threading.Thread(target=col.run, daemon=True)
        th.start()
        t0 = time.monotonic()
        while col.state != "running" and th.is_alive() and time.monotonic() - t0 < 20:
            time.sleep(0.2)
        check(col.state == "running", "session started")
        time.sleep(2)
        child = subprocess.Popen(["cmd.exe", "/c", "whoami"], stdout=subprocess.DEVNULL)
        child.wait()
        # network: a child process (our own traffic is excluded) asks Loki on the trainer, if etwlab.json names it
        url = None
        try:
            with open(r"C:\ProgramData\etwlab\etwlab.json", encoding="utf-8-sig") as fh:
                url = json.load(fh).get("LokiUrl")
        except (OSError, ValueError):
            pass
        net = subprocess.Popen(["curl.exe", "-s", "-m", "5", url + "/ready"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) if url else None
        if net:
            net.wait()
        # the canary file is written and read by a CHILD: our own file I/O is excluded by design (rule a)
        canary = os.path.join(tempfile.gettempdir(), f"etwcollect-canary-{os.getpid()}.txt")
        subprocess.run(["cmd.exe", "/c", f"echo canary> {canary} & type {canary}"], stdout=subprocess.DEVNULL)   # TEMP: no spaces
        time.sleep(8)
        RealtimeSession.stop_by_name("etwlab-selftest")
        th.join(60)
        check(col.state == "stopped", "collector stopped and flushed")

        def lines(stem):
            with open(os.path.join(out, stem + ".json"), encoding="utf-8") as fh:
                return [json.loads(x) for x in fh if x.strip()]
        kp, kf, kn = lines("kproc"), lines("kfile"), lines("knet")
        print(f"  lines kproc={len(kp)} kfile={len(kf)} knet={len(kn)}  excluded={dict(col.ex.counts)}  "
              f"schemas={len(col.schemas.cache)} slow={col.slow_events} decode_errors={col.decode_errors} "
              f"callback_errors={col.callback_errors} last_error={col.last_error}")
        check(any(e["EventName"].startswith("ProcessStart") and e["XmlEventData"].get("ParentProcessID") == str(child.pid)
                  and e["XmlEventData"].get("ImageName", "").lower().endswith("whoami.exe") for e in kp),
              f"ProcessStart of whoami.exe under our cmd.exe (pid {child.pid})")
        check(col.names.get(child.pid) == "cmd", "cmd.exe named although its ProcessStart came from our own PID")
        if net:
            check(any(e["XmlEventData"].get("PID") == str(net.pid) for e in kn),
                  f"Kernel-Network events owned by curl.exe (pid {net.pid}, payload PID)")
            sample = next(e for e in kn if e["XmlEventData"].get("PID") == str(net.pid))
            print(f"  knet sample: {sample['EventName']} {sample['XmlEventData']}")
        else:
            print("  skip  no LokiUrl in etwlab.json: Kernel-Network not exercised")
        cname = os.path.basename(canary).lower()
        check(any(e["EventName"].startswith("Create") and e["XmlEventData"].get("FileName", "").lower().endswith(cname)
                  for e in kf), "Create of the canary file")
        check(any(e["EventName"] in ("Write", "Read") for e in kf), "file Read/Write events")
        own = os.getpid()
        check(not any(e["ProcessID"] == own for e in kp + kf), "nothing from our own PID was written")
        check(col.ex.counts.get("kfile.self_pid", 0) + col.ex.counts.get("kfile.own_file", 0) > 0,
              "our own file I/O was seen and dropped")
        check(col.callback_errors == 0, "no callback errors")
        conv = _find_converter()
        if conv:
            s = _load_converter(conv).convert(os.path.join(root, "raw"), "test", "SelfTestLive", os.path.join(root, "out"),
                                              col.dmap)
            print(f"  converter: edges {s['edges']} unresolved {s['unresolved']} unmapped_top "
                  f"{dict(list(s['unmapped_top'].items())[:5])}")
            check(s["edges"].get("Start", 0) >= 1 and s["edges"].get("Load", 0) >= 1, "converter: Start + Load edges")
        print(f"  capture kept in {root}")
        os.remove(canary)
    except AssertionError as e:
        print(f"  FAIL {e}")
        print("[SELFTEST --live FAILED]")
        return 1
    print("[SELFTEST --live OK] real session, child + canary seen, own I/O excluded")
    return 0


if __name__ == "__main__":
    sys.exit(live() if "--live" in sys.argv else offline())
