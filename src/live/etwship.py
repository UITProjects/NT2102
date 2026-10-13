"""etwship.py - ship SilkETW JSON-lines files to the OCR-APT live server. Stdlib only (VM embeddable Python).

Live mode (on the VM, while SilkService writes C:\\lab-out\\etw\\raw\\test\\live-<UTC>\\*.json):
    python etwship.py --dir C:\\lab-out\\etw\\raw\\test\\live-20261011T100000Z --server http://10.21.2.1:8000
  Every --interval s: read each *.json from its last offset up to the last complete line, POST
  {session, clock_ms, pid, dos_map, batches:[{stream, offset_start, lines, ends}]}. A POST is sent even
  with no new lines (heartbeat): the server closes windows by the VM clock, so quiet periods still advance.
  Offsets are saved to <dir>\\.ship-offsets.json only after the server confirmed them -> no loss, and
  duplicates after a retry are dropped server-side (it keys on byte offsets).

Replay mode (on the host, no VM): send a recorded run with a virtual clock, --speed times faster.
    python etwship.py --replay src/out/etw/experiments/<id>/raw/train/<run> --server http://127.0.0.1:8000 --speed 120
  (./replay.sh wraps this.)
  The dos-map (\\device\\harddiskvolumeN -> c:) is inferred from the raw paths unless --dos-map is given.

    python etwship.py --print-dos-map      (on the VM: the mapping the live mode sends)
    python etwship.py --selftest
"""
import argparse, glob, gzip, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

MAX_LINES, MAX_BYTES = 2000, 1 << 20


def dos_device_map():
    """Same as etw2nodlink.dos_device_map: '\\device\\harddiskvolume4' -> 'c:'. Empty off Windows."""
    m = {}
    if os.name != "nt":
        return m
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ctypes.windll.kernel32.QueryDosDeviceW(c + ":", buf, 1024):
            m[buf.value.lower()] = c.lower() + ":"
    return m


def post(server, body, token=None, timeout=30):
    """-> (status, json). Network errors raise OSError."""
    data = gzip.compress(json.dumps(body).encode("utf-8"))
    req = urllib.request.Request(server.rstrip("/") + "/ingest", data=data, method="POST", headers={
        "Content-Type": "application/json", "Content-Encoding": "gzip", **({"X-Lab-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def read_new(path, offset):
    """Complete lines after `offset`. -> (lines, ends) with ends = byte offset after each line's newline."""
    lines, ends = [], []
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read(MAX_BYTES)
    cut = chunk.rfind(b"\n")
    if cut < 0:
        return lines, ends                        # no complete line yet (SilkService mid-write)
    pos = offset
    for raw in chunk[:cut + 1].split(b"\n")[:-1]:
        pos += len(raw) + 1
        text = raw.rstrip(b"\r").decode("utf-8", errors="replace")
        if text.strip():
            lines.append(text)
            ends.append(pos)
        elif ends:
            ends[-1] = pos                         # fold blank lines into the previous line's span
        if len(lines) >= MAX_LINES:
            break
    return lines, ends


class Shipper:
    def __init__(self, a):
        self.a = a
        self.session = a.session or os.path.basename(os.path.normpath(a.dir))
        self.state_path = os.path.join(a.dir, ".ship-offsets.json")
        self.offsets = json.load(open(self.state_path)) if os.path.exists(self.state_path) else {}
        self.dmap = dos_device_map()
        self.hello = {"session": self.session, "run": os.path.basename(os.path.normpath(a.dir)),
                      "mode": "live", "host": os.environ.get("COMPUTERNAME", ""),
                      "pid": os.getpid(), "dos_map": self.dmap}

    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.offsets, fh)
        os.replace(tmp, self.state_path)

    def tick(self):
        batches = []
        for path in sorted(glob.glob(os.path.join(self.a.dir, "*.json"))):
            stream = os.path.splitext(os.path.basename(path))[0]
            off = self.offsets.get(stream, 0)
            lines, ends = read_new(path, off)
            if lines:
                batches.append({"stream": stream, "offset_start": off, "lines": lines, "ends": ends})
        body = dict(self.hello, clock_ms=int(time.time() * 1000), batches=batches)
        code, resp = post(self.a.server, body, self.a.token)
        if code == 409:                            # server lost data or restarted: resume where it is
            for s, off in resp.get("expected", {}).items():
                print(f"[etwship] server expects {s} at {off} (we had {self.offsets.get(s, 0)}); rewinding")
                self.offsets[s] = off
            return 0
        if code != 200:
            raise OSError(f"HTTP {code}: {resp}")
        for b in batches:
            self.offsets[b["stream"]] = b["ends"][-1]
        self.save()
        return sum(len(b["lines"]) for b in batches)

    def run(self):
        print(f"[etwship] session {self.session}: {self.a.dir} -> {self.a.server}  pid={os.getpid()}  dos_map={self.dmap}")
        backoff, sent, last_print = 1.0, 0, time.time()
        while True:
            try:
                n = self.tick()
                sent += n
                backoff = 1.0
                if time.time() - last_print > 30:
                    print(f"[etwship] {datetime.now().strftime('%H:%M:%S')} shipped {sent} lines, offsets {self.offsets}")
                    last_print = time.time()
                if n < MAX_LINES:                  # caught up -> wait; otherwise send the next chunk now
                    time.sleep(self.a.interval)
            except (OSError, ValueError) as e:
                print(f"[etwship] send failed ({e}); retry in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


# --------------------------------------------------------------------------- replay
_TS = re.compile(r'"TimeStamp"\s*:\s*"([^"]+)"')


def ts_ms(s):
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.strip().replace("Z", "+00:00"))
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def infer_dos_map(paths):
    """'\\Device\\HarddiskVolume4\\Windows\\...' in the raw data -> {'\\device\\harddiskvolume4': 'c:'}."""
    vols = {}
    rx = re.compile(r"(\\\\device\\\\harddiskvolume\d+)\\\\windows\\\\", re.I)
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                for m in rx.finditer(line):
                    v = m.group(1).replace("\\\\", "\\").lower()
                    vols[v] = vols.get(v, 0) + 1
                if i > 200000:
                    break
    return {max(vols, key=vols.get): "c:"} if vols else {}


def replay(a):
    files = sorted(glob.glob(os.path.join(a.replay, "*.json")))
    if not files:
        sys.exit(f"no *.json in {a.replay}")
    dmap = dict(kv.split("=", 1) for kv in a.dos_map) if a.dos_map else infer_dos_map(files)
    session = a.session or "replay-" + os.path.basename(os.path.normpath(a.replay))
    # per stream, in FILE order (like a live tail): (ts, end_offset, line). Offsets must stay monotonic.
    streams = {}
    for p in files:
        stream, pos, rows = os.path.splitext(os.path.basename(p))[0], 0, []
        with open(p, "rb") as fh:
            for raw in fh:
                pos += len(raw)
                text = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")
                m = _TS.search(text)
                if m:
                    rows.append((ts_ms(m.group(1)), pos, text))
        streams[stream] = rows
    all_ts = [r[0] for rows in streams.values() for r in rows]
    t_first, t_last = min(all_ts), max(all_ts)
    print(f"[etwship] replay {len(all_ts)} lines, {(t_last - t_first) / 60000:.1f} min of capture at "
          f"{a.speed}x -> {session}  dos_map={dmap}")
    hello = {"session": session, "run": os.path.basename(os.path.normpath(a.replay)), "mode": "replay",
             "host": "replay", "pid": a.exclude_pid, "dos_map": dmap}
    offsets = {s: 0 for s in streams}             # stream -> offset_start of the next batch
    idx = {s: 0 for s in streams}
    vclock, step = t_first, int(a.interval * 1000 * a.speed)
    wall0 = time.time()
    while True:
        vclock += step
        batches = []
        for s, rows in streams.items():
            j = idx[s]
            while j < len(rows) and rows[j][0] < vclock:   # a SilkETW file is written in time order
                j += 1
            if j > idx[s]:
                part = rows[idx[s]:j]
                batches.append({"stream": s, "offset_start": offsets[s],
                                "lines": [r[2] for r in part], "ends": [r[1] for r in part]})
                idx[s] = j
        done = all(idx[s] >= len(rows) for s, rows in streams.items())
        code, resp = post(a.server, dict(hello, clock_ms=vclock, batches=batches), a.token)
        if code != 200:
            sys.exit(f"[etwship] server answered {code}: {resp}")
        for b in batches:
            offsets[b["stream"]] = b["ends"][-1]
        if done and vclock > t_last + a.tail_s * 1000:
            break
        if a.speed > 0:
            time.sleep(max(0.0, wall0 + (vclock - t_first) / 1000 / a.speed - time.time()))
    print(f"[etwship] replay done ({time.time() - wall0:.0f} s wall)")


# --------------------------------------------------------------------------- selftest
def selftest():
    """Fake /ingest server on localhost: live mode ships complete lines only, survives a 409 and a
    server outage, and does not resend confirmed lines. 'It runs' only."""
    import tempfile, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got, have = [], {}
    state = {"fail": 0, "gap_once": True}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(gzip.decompress(self.rfile.read(int(self.headers["Content-Length"]))))
            if state["fail"]:
                state["fail"] -= 1
                code, resp = 503, {}
            else:
                code, resp = 200, {}
                for b in body["batches"]:
                    exp = have.get(b["stream"], 0)
                    if b["offset_start"] > exp:
                        code, resp = 409, {"expected": {b["stream"]: exp}}
                        break
                    for line, end in zip(b["lines"], b["ends"]):
                        if end > exp:
                            got.append(line)
                            have[b["stream"]] = exp = end
            out = json.dumps(resp).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    with tempfile.TemporaryDirectory() as d:
        a = argparse.Namespace(dir=d, server=url, token=None, session="t", interval=0.01)
        f = os.path.join(d, "kproc.json")
        with open(f, "wb") as fh:
            fh.write(b'{"a":1}\r\n{"a":2}\r\n{"a":3')     # third line incomplete
        sh = Shipper(a)
        assert sh.tick() == 2 and got == ['{"a":1}', '{"a":2}']
        with open(f, "ab") as fh:
            fh.write(b'}\r\n{"a":4}\r\n')
        state["fail"] = 1
        try:
            sh.tick()
            raise AssertionError("503 should raise")
        except OSError:
            pass
        assert sh.tick() == 2 and got[-2:] == ['{"a":3}', '{"a":4}']
        assert sh.tick() == 0 and len(got) == 4                     # heartbeat, nothing resent
        # server "restarts" and forgets: our next batch has a gap -> 409 -> rewind -> resend all
        have.clear()
        with open(f, "ab") as fh:
            fh.write(b'{"a":5}\r\n')
        assert sh.tick() == 0                                       # 409 handled, offsets rewound to 0
        assert sh.tick() == 5 and got[-5:] == ['{"a":%d}' % i for i in range(1, 6)]
        assert json.load(open(os.path.join(d, ".ship-offsets.json")))["kproc"] == os.path.getsize(f)
    srv.shutdown()
    print("[SELFTEST OK] complete lines only, retry after 503, no resend, 409 rewind, offsets persisted")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="live: SilkETW run folder to tail")
    ap.add_argument("--replay", help="replay: recorded run folder (*.json)")
    ap.add_argument("--server", default="http://10.21.2.1:8000")
    ap.add_argument("--session", help="default: run folder name")
    ap.add_argument("--token", default=os.environ.get("LAB_TOKEN"))
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between POSTs (virtual seconds in replay)")
    ap.add_argument("--speed", type=float, default=60, help="replay: virtual seconds per wall second (use >= 1; 0 hangs, ~120 is safe)")
    ap.add_argument("--tail-s", type=float, default=360, help="replay: keep the clock running after the last event")
    ap.add_argument("--dos-map", nargs="*", help=r"replay: \device\harddiskvolume4=c: (default: inferred)")
    ap.add_argument("--exclude-pid", type=int, default=None,
                    help="replay: PID whose edges the server drops (the live shipper's own PID is sent automatically)")
    ap.add_argument("--print-dos-map", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.print_dos_map:
        return print(json.dumps(dos_device_map(), indent=1))
    if a.replay:
        return replay(a)
    if not a.dir:
        ap.error("--dir (live) or --replay is required")
    try:
        Shipper(a).run()
    except KeyboardInterrupt:
        print("[etwship] stopped (Ctrl+C); offsets saved, re-run resumes where it stopped")


if __name__ == "__main__":
    main()
