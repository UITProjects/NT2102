"""lokiclient.py - the few Loki HTTP calls the lab needs (stdlib only). Shared by loki_pull.py (training
pull), live/loki_source.py (live demo) and live/loki_push.py (test feed).

Label scheme (what Alloy on the VM writes; src/ansible/roles/win_alloy/templates/config.alloy.j2):
    labels (indexed)       job="silketw"|"etw-meta"|"workload-ledger", host, split, run, provider (kproc|kfile|knet)
    structured metadata    event (EventName), pid (ProcessID), process (ProcessName)  -- filterable, not indexed
    line                   the raw SilkETW JSON line, unchanged

Times: Loki speaks epoch NANOSECONDS. Every function here takes and returns ns ints.

    python lokiclient.py --selftest
"""
import gzip, json, re, time, urllib.error, urllib.parse, urllib.request
from collections import Counter
from datetime import datetime, timezone

STREAM_KEYS = ("job", "host", "split", "run", "provider")   # the labels we index; Loki merges metadata into
                                                            # the returned stream labels, so key on these only
_FRAC = re.compile(r"\.(\d+)")


# --------------------------------------------------------------------------- time
def ts_ns(s):
    """ISO 8601 with any number of fractional digits -> epoch ns. Naive = UTC (same rule as the converter).
    '2026-10-09T14:56:34.0985971+07:00' -> 1791532594098597100. Keeps all 9 digits, like Alloy's RFC3339Nano."""
    s = s.strip().replace("Z", "+00:00")
    frac = 0
    m = _FRAC.search(s)
    if m:
        frac = int((m.group(1) + "000000000")[:9])
        s = s[:m.start()] + s[m.end():]
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000_000 + frac


_REL = re.compile(r"^(?:now)?\s*-?\s*(\d+(?:\.\d+)?)\s*([smhdw])$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_time(s, now_ns=None):
    """'now' | 'now-2h' | '90m' (= now-90m) | ISO 8601 (offset optional, naive = UTC) | epoch ns -> epoch ns."""
    now_ns = time.time_ns() if now_ns is None else now_ns
    s = str(s).strip()
    if s == "now":
        return now_ns
    m = _REL.match(s)
    if m:
        return now_ns - int(float(m.group(1)) * _UNIT[m.group(2)] * 1_000_000_000)
    if s.isdigit():
        return int(s)
    return ts_ns(s)


def iso(ns, tz=timezone.utc):
    return datetime.fromtimestamp(ns / 1e9, tz=tz).strftime("%Y-%m-%d %H:%M:%S%z")


def selector(**labels):
    """selector(job="silketw", run="x-1") -> '{job="silketw", run="x-1"}' (None values skipped)."""
    parts = ['%s="%s"' % (k, str(v).replace("\\", "\\\\").replace('"', '\\"')) for k, v in labels.items() if v]
    return "{" + ", ".join(parts) + "}"


# --------------------------------------------------------------------------- HTTP
class Loki:
    def __init__(self, url, timeout=60, user=None, password=None):
        self.url, self.timeout = url.rstrip("/"), timeout
        self.headers = {}
        if user:
            import base64
            self.headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def get(self, path, **params):
        q = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
        req = urllib.request.Request(f"{self.url}{path}?{q}", headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise OSError(f"Loki {path} HTTP {e.code}: {e.read()[:500].decode(errors='replace')}") from None

    def ready(self):
        req = urllib.request.Request(self.url + "/ready", headers=self.headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read().decode().strip() == "ready"

    def label_values(self, name, start_ns, end_ns, query=None):
        """Values of one label in [start, end], optionally only for streams matching `query`. Used for discovery
        instead of /series: /series misses data whose event time is older than query_ingesters_within (3 h)
        while it is still in the ingester (a replayed or late-shipped capture); label values do not."""
        return sorted(self.get(f"/loki/api/v1/label/{name}/values", start=start_ns, end=end_ns, query=query)
                      .get("data") or [])

    def series(self, match, start_ns, end_ns):
        """-> list of label dicts (only STREAM_KEYS), de-duplicated."""
        data = self.get("/loki/api/v1/series", **{"match[]": match, "start": start_ns, "end": end_ns})["data"]
        seen = {tuple((k, d.get(k, "")) for k in STREAM_KEYS) for d in data}
        return [dict(t) for t in sorted(seen)]

    def query_range(self, query, start_ns, end_ns, limit=5000, direction="forward"):
        """One page -> list of (ts_ns, labels(STREAM_KEYS), line), sorted in `direction`. end is exclusive."""
        res = self.get("/loki/api/v1/query_range", query=query, start=start_ns, end=end_ns, limit=limit,
                       direction=direction)["data"]["result"]
        out = []
        for s in res:
            lab = {k: s["stream"].get(k, "") for k in STREAM_KEYS}
            out.extend((int(v[0]), lab, v[1]) for v in s["values"])
        out.sort(key=lambda r: r[0], reverse=(direction == "backward"))
        return out

    def query_all(self, query, start_ns, end_ns, page=5000):
        """Every entry in [start, end), oldest first, paged by time.

        Paging: the next page starts AT the last timestamp seen (start is inclusive), because other entries
        may share it. The ones already yielded at that timestamp are skipped by count, so a boundary never
        drops or repeats a line."""
        at_boundary, start = Counter(), start_ns
        while start < end_ns:
            rows = self.query_range(query, start, end_ns, limit=page)
            skip = Counter(at_boundary)
            fresh = []
            for ts, lab, line in rows:
                key = (ts, tuple(lab.values()), line)
                if ts == start and skip[key] > 0:
                    skip[key] -= 1
                    continue
                fresh.append((ts, lab, line))
            yield from fresh
            if len(rows) < page:
                return
            last = rows[-1][0]
            if last == start and not fresh:
                raise RuntimeError(f"more than {page} entries share ts {start}; raise the page size")
            if last != start:
                at_boundary = Counter()
            at_boundary.update((ts, tuple(lab.values()), line) for ts, lab, line in fresh if ts == last)
            start = last

    def edge_ts(self, query, start_ns, end_ns):
        """(first_ns, last_ns) of a selector inside [start, end), or None if empty."""
        f = self.query_range(query, start_ns, end_ns, limit=1, direction="forward")
        if not f:
            return None
        b = self.query_range(query, start_ns, end_ns, limit=1, direction="backward")
        return f[0][0], b[0][0]

    def count_by(self, query, by, start_ns, end_ns):
        """{label value: line count} over [start, end] via count_over_time (instant query at end)."""
        rng = max(1, -(-(end_ns - start_ns) // 1_000_000_000))
        q = f"sum by ({by}) (count_over_time({query}[{rng}s]))"
        res = self.get("/loki/api/v1/query", query=q, time=end_ns)["data"]["result"]
        return {r["metric"].get(by, ""): int(float(r["value"][1])) for r in res}

    def push(self, streams, retries=8):
        """streams: [{"stream": {labels}, "values": [[ts_ns_str, line, {metadata}?], ...]}] -> HTTP 204.
        429 (rate limit) / 5xx are retried with backoff, like Alloy's loki.write. Re-sending a batch Loki
        partly accepted is safe: it drops entries identical to ones it has (same stream, ts and line)."""
        body = gzip.compress(json.dumps({"streams": streams}).encode("utf-8"))
        for attempt in range(retries + 1):
            req = urllib.request.Request(self.url + "/loki/api/v1/push", data=body, method="POST", headers={
                "Content-Type": "application/json", "Content-Encoding": "gzip", **self.headers})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                msg = e.read()[:500].decode(errors="replace")
                if (e.code == 429 or e.code >= 500) and attempt < retries:
                    time.sleep(min(0.5 * 2 ** attempt, 15))
                    continue
                raise OSError(f"Loki push HTTP {e.code}: {msg}") from None


# --------------------------------------------------------------------------- fake Loki (selftests)
class FakeLoki:
    """In-memory Loki for selftests: push, series, query_range (forward/backward, limit, label match),
    count_over_time by one label. Not a LogQL engine: matchers are exact label equality only."""

    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.entries = []          # (ts_ns, labels dict, line)
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                out = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_POST(self):
                if self.path == "/flush":
                    return self._send(204)
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                for s in json.loads(raw)["streams"]:
                    for v in s["values"]:
                        lab = dict(s["stream"], **(v[2] if len(v) > 2 else {}))
                        fake.entries.append((int(v[0]), lab, v[1]))
                self._send(204)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                if u.path == "/ready":
                    out = b"ready"
                    self.send_response(200); self.send_header("Content-Length", "5"); self.end_headers()
                    return self.wfile.write(out)
                if u.path == "/loki/api/v1/series":
                    sel = fake.match(q["match[]"])
                    labs = {json.dumps(l, sort_keys=True) for t, l, _ in fake.entries
                            if sel(l) and int(q["start"]) <= t <= int(q["end"])}
                    return self._send(200, {"status": "success", "data": [json.loads(x) for x in sorted(labs)]})
                m = re.match(r"^/loki/api/v1/label/(\w+)/values$", u.path)
                if m:
                    sel = fake.match(q.get("query", "{}"))
                    vals = {l.get(m.group(1)) for t, l, _ in fake.entries
                            if sel(l) and int(q["start"]) <= t <= int(q["end"]) and l.get(m.group(1))}
                    return self._send(200, {"status": "success", "data": sorted(vals)})
                if u.path == "/loki/api/v1/query_range":
                    sel = fake.match(q["query"])
                    rows = [e for e in fake.entries if sel(e[1]) and int(q["start"]) <= e[0] < int(q["end"])]
                    rows.sort(key=lambda e: e[0], reverse=q.get("direction") == "backward")
                    rows = rows[:int(q.get("limit", 100))]
                    streams = {}
                    for t, l, line in rows:
                        streams.setdefault(json.dumps(l, sort_keys=True), []).append([str(t), line])
                    res = [{"stream": json.loads(k), "values": v} for k, v in streams.items()]
                    return self._send(200, {"status": "success", "data": {"resultType": "streams", "result": res}})
                if u.path == "/loki/api/v1/query":
                    m = re.match(r"sum by \((\w+)\) \(count_over_time\((\{.*\})\[(\d+)s\]\)\)", q["query"])
                    by, sel, rng = m.group(1), fake.match(m.group(2)), int(m.group(3)) * 1_000_000_000
                    end = int(q["time"])
                    c = Counter(l.get(by, "") for t, l, _ in fake.entries if sel(l) and end - rng < t <= end)
                    res = [{"metric": {by: k}, "value": [end / 1e9, str(v)]} for k, v in c.items()]
                    return self._send(200, {"status": "success", "data": {"resultType": "vector", "result": res}})
                self._send(404, {"error": u.path})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @staticmethod
    def match(sel):
        want = dict(re.findall(r'(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"', sel))
        return lambda l: all(l.get(k) == v for k, v in want.items())

    def close(self):
        self.srv.shutdown()


def selftest():
    assert ts_ns("2026-10-09T14:56:34.0985971+07:00") == ts_ns("2026-10-09T07:56:34.0985971Z")
    assert ts_ns("2026-10-09T07:56:34.0985971Z") % 1_000_000_000 == 98_597_100
    assert ts_ns("2026-10-09T07:56:34") == ts_ns("2026-10-09T07:56:34+00:00")     # naive = UTC
    assert parse_time("now-2h", now_ns=10**13) == 10**13 - 7200 * 10**9 == parse_time("2h", now_ns=10**13)
    assert selector(job="silketw", run=None, split="train") == '{job="silketw", split="train"}'
    fake = FakeLoki()
    try:
        lk = Loki(fake.url)
        assert lk.ready()
        lab = {"job": "silketw", "host": "h", "split": "train", "run": "r1", "provider": "kproc"}
        # 7 entries, three share ts=300 and two of those are identical lines (must both survive)
        vals = [[str(t), l, {"event": "E"}] for t, l in
                [(100, "a"), (200, "b"), (300, "c"), (300, "d"), (300, "d"), (400, "e"), (500, "f")]]
        assert lk.push([{"stream": lab, "values": vals}]) == 204
        got = list(lk.query_all('{run="r1"}', 0, 10**6, page=4))      # page boundary falls inside ts=300
        assert [(t, l) for t, _, l in got] == [(100, "a"), (200, "b"), (300, "c"), (300, "d"), (300, "d"),
                                               (400, "e"), (500, "f")], got
        try:                                          # 3 entries at one ts cannot be paged with 2 per page
            list(lk.query_all('{run="r1"}', 0, 10**6, page=2))
            raise AssertionError("tie larger than a page must raise")
        except RuntimeError:
            pass
        assert lk.edge_ts('{run="r1"}', 0, 10**6) == (100, 500)
        assert lk.series('{job="silketw"}', 0, 10**6) == [lab]
        assert lk.label_values("provider", 0, 10**6, query='{run="r1"}') == ["kproc"]
        assert lk.count_by('{run="r1"}', "provider", 100, 500) == {"kproc": 7}
    finally:
        fake.close()
    print("[SELFTEST OK] ns times, relative ranges, paging across a tied page boundary, series, counts")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(__doc__)
