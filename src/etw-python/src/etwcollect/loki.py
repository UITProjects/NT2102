"""loki.py - push our run files to Loki on the trainer (replaces Grafana Alloy, DEVIATIONS D10).

Loki contract = what Alloy produced (src/ansible/roles/win_alloy/templates/config.alloy.j2), so the trainer
side (lokiclient.py, loki_pull.py, loki_source.py) is unchanged:

    events   labels {job="silketw", host, split, run, provider}   provider = file stem (kproc|kfile|knet)
             structured metadata {event, pid, process}            (filterable, not indexed)
             entry timestamp = the line's own TimeStamp (event time), line = the file line unchanged
    dos map  labels {job="etw-meta", host, split, run}            one JSON line per run (dosmap.meta)
    ledger   labels {job="workload-ledger", host, exp}            one entry per CSV row, ts = its "utc" column

Delivery works like Alloy's file tail: the shipper reads each tracked file from a saved byte offset
(positions file), pushes whole lines in batches, and only moves the offset after Loki said 204. A Loki
outage or a collector restart therefore delays lines but does not lose them (Loki accepts entries up to
reject_old_samples_max_age = the 3-day retention). Re-sending a batch Loki partly took is harmless: Loki
drops entries identical to ones it has (same stream, timestamp and line).

Push body (POST /loki/api/v1/push, gzip JSON):
    {"streams": [{"stream": {"job": "silketw", ..., "provider": "kfile"},
                  "values": [["1761377445872583100", "<line>", {"event": "Create", "pid": "420", "process": "x"}]]}]}
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

NS = 1_000_000_000
MAX_BATCH = 1 << 20                      # 1 MiB of line bytes per push (Alloy: batch_size 1MiB)
_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?\s*(Z|[+-]\d\d:\d\d)?$")


class LokiError(OSError):
    def __init__(self, code: int, msg: str):
        super().__init__(f"Loki push HTTP {code}: {msg}")
        self.code = code
        # 429 = rate limited, 5xx = Loki trouble, 0 = no connection: try again later. Other 4xx = Loki
        # refuses these lines (e.g. older than the retention): retrying would stall the file forever.
        self.retryable = code == 0 or code == 429 or code >= 500


def ts_ns(s: str) -> int:
    """'2026-10-25T07:30:45.8725831+00:00' -> 1761377445872583100. Accepts 0-9 fractional digits, Z or an
    offset ('...+07:00' = 7 h ahead of UTC, so the same instant has a smaller UTC hour)."""
    m = _TS.match(str(s).strip())
    if not m:
        raise ValueError(f"not an ISO timestamp: {s!r}")
    off = m.group(3) or "+00:00"
    base = datetime.fromisoformat(m.group(1) + ("+00:00" if off == "Z" else off))
    frac = (m.group(2) or "0")[:9].ljust(9, "0")
    return int(base.timestamp()) * NS + int(frac)


class LokiClient:
    def __init__(self, url: str, timeout: float = 15.0):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def push(self, streams: list[dict]) -> int:
        body = gzip.compress(json.dumps({"streams": streams}, ensure_ascii=False).encode("utf-8"))
        req = urllib.request.Request(self.url + "/loki/api/v1/push", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Content-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status
        except urllib.error.HTTPError as e:
            raise LokiError(e.code, e.read()[:300].decode(errors="replace")) from None
        except (urllib.error.URLError, OSError) as e:
            raise LokiError(0, str(e)) from None


def labels_for(path: str, host: str) -> dict[str, str]:
    """raw_root\\<split>\\<run>\\<stem>.json -> the Loki stream labels Alloy derived from the same path."""
    p = os.path.normpath(path)
    run_dir, fn = os.path.split(p)
    split_dir, run = os.path.split(run_dir)
    split = os.path.basename(split_dir)
    return {"job": "silketw", "host": host, "split": split, "run": run, "provider": os.path.splitext(fn)[0]}


def entry(line: bytes, now_ns: int) -> tuple[list, bool]:
    """One file line -> [ts_ns, line, metadata]. A line that does not parse keeps the read time (Alloy's
    action_on_failure = skip); the converter counts such lines as bad on the trainer."""
    text = line.decode("utf-8", errors="replace").rstrip("\r\n")
    try:
        ev = json.loads(text)
        return [str(ts_ns(ev["TimeStamp"])), text, {"event": str(ev.get("EventName", "")),
                                                    "pid": str(ev.get("ProcessID", "")),
                                                    "process": str(ev.get("ProcessName", ""))}], True
    except (ValueError, KeyError, TypeError):
        return [str(now_ns), text], False


class Shipper:
    """Tails tracked files from saved offsets and pushes whole lines. Positions file:
        {"files": {"C:\\\\...\\\\kfile.json": {"offset": 123, "labels": {...}, "closed": false}},
         "meta":  {"test/live-20261026T080000Z": true}}
    Not thread-safe: one thread calls step(); track()/close_run() are called from the same collector."""

    def __init__(self, client: LokiClient | None, positions_path: str, host: str, max_batch: int = MAX_BATCH,
                 now_ns=time.time_ns, log=print):
        self.client, self.pos_path, self.host = client, positions_path, host
        self.max_batch, self.now_ns, self.log = max_batch, now_ns, log
        self.state = {"files": {}, "meta": {}}
        if os.path.exists(positions_path):
            try:
                with open(positions_path, encoding="utf-8") as fh:
                    self.state = json.load(fh)
            except (OSError, ValueError):
                self.log(f"[ship] positions file unreadable, starting from offset 0: {positions_path}")
        self.state.setdefault("files", {})
        self.state.setdefault("meta", {})
        self.pushed_lines = 0
        self.bad_lines = 0
        self.rejected = 0
        self.last_push_ns = None
        self.last_error = None
        self.backoff_until = 0.0
        self.backoff = 1.0

    # ---------------------------------------------------------------- bookkeeping
    def _save(self) -> None:
        tmp = self.pos_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1)
        os.replace(tmp, self.pos_path)

    def track(self, path: str) -> None:
        if path not in self.state["files"]:
            self.state["files"][path] = {"offset": 0, "labels": labels_for(path, self.host), "closed": False}
            self._save()

    def close_run(self, run_dir: str) -> None:
        """The collector stopped writing this run: once shipped to the end, its entries are forgotten."""
        for p, f in self.state["files"].items():
            if os.path.dirname(os.path.normpath(p)) == os.path.normpath(run_dir):
                f["closed"] = True
        self._save()

    def backlog(self) -> dict[str, int]:
        """Unshipped bytes per tracked file (for collector-status.json)."""
        out = {}
        for p, f in self.state["files"].items():
            try:
                out[p] = max(0, os.path.getsize(p) - f["offset"])
            except OSError:
                out[p] = 0
        return out

    # ---------------------------------------------------------------- pushing
    def _push(self, streams: list[dict]) -> bool:
        """True = Loki has the lines (or refused them for good); False = try again later."""
        if self.client is None:
            return False
        try:
            self.client.push(streams)
            self.last_push_ns, self.last_error, self.backoff = self.now_ns(), None, 1.0
            return True
        except LokiError as e:
            self.last_error = str(e)
            if not e.retryable:
                self.rejected += 1
                self.log(f"[ship] Loki refused a batch, skipping it: {e}")
                return True
            self.backoff_until = time.monotonic() + self.backoff
            self.backoff = min(self.backoff * 2, 30.0)
            return False

    def push_meta(self, split: str, run: str, dmap: dict) -> bool:
        key = f"{split}/{run}"
        if self.state["meta"].get(key):
            return True
        line = json.dumps(dmap, separators=(",", ":"))
        ok = self._push([{"stream": {"job": "etw-meta", "host": self.host, "split": split, "run": run},
                          "values": [[str(self.now_ns()), line]]}])
        if ok:
            self.state["meta"][key] = True
            self._save()
        return ok

    def step(self) -> int:
        """Ship at most one batch per tracked file. Returns the number of lines Loki accepted."""
        if time.monotonic() < self.backoff_until:
            return 0
        sent = 0
        for path in list(self.state["files"]):
            f = self.state["files"][path]
            try:
                size = os.path.getsize(path)
            except OSError:                              # run folder deleted (live runs are pruned after a day)
                del self.state["files"][path]
                self._save()
                continue
            if f["offset"] >= size:
                if f["closed"]:
                    del self.state["files"][path]
                    self._save()
                continue
            with open(path, "rb") as fh:
                fh.seek(f["offset"])
                chunk = fh.read(self.max_batch)
            cut = chunk.rfind(b"\n") + 1                 # whole lines only; a partial last line waits
            if cut == 0:
                if len(chunk) == self.max_batch:         # one line longer than a batch: ship it alone
                    cut = len(chunk)
                elif f["closed"]:                        # a crashed run left half a line: never completes
                    f["offset"] = size
                    self.bad_lines += 1
                    self._save()
                    continue
                else:
                    continue
            now = self.now_ns()
            values = []
            for raw in chunk[:cut].splitlines(keepends=True):
                if not raw.strip():
                    continue
                v, ok = entry(raw, now)
                values.append(v)
                self.bad_lines += not ok
            refused = self.rejected
            if values and not self._push([{"stream": f["labels"], "values": values}]):
                return sent                              # Loki down: keep the offset, retry after backoff
            f["offset"] += cut
            self._save()
            if self.rejected == refused:                 # a refused batch is skipped, not counted as shipped
                sent += len(values)
                self.pushed_lines += len(values)
        return sent

    def drain(self, timeout_s: float) -> bool:
        """Ship until every tracked file is at its end, or the timeout passes. True = nothing left."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not any(self.backlog().values()):
                return True
            if self.step() == 0:
                time.sleep(0.5)
        return not any(self.backlog().values())


# --------------------------------------------------------------------------- the 07 workload ledger
_LEDGER_TS = re.compile(r'^"(\d{4}-[^"]+)"')


def push_ledger(client: LokiClient, host: str, exp: str, labels_dir: str, now_ns=time.time_ns) -> int:
    """<exp>\\labels\\*.csv -> {job="workload-ledger", host, exp}. Each row's timestamp is its first column
    ("utc"); the header row has none and gets the push time (loki_pull.py puts it back on top)."""
    values = []
    for p in sorted(glob.glob(os.path.join(labels_dir, "*.csv"))):
        with open(p, encoding="utf-8-sig") as fh:
            for row in fh:
                row = row.rstrip("\r\n")
                if not row:
                    continue
                m = _LEDGER_TS.match(row)
                t = now_ns()
                if m:
                    try:
                        t = ts_ns(m.group(1))
                    except ValueError:
                        pass
                values.append([str(t), row])
    labels = {"job": "workload-ledger", "host": host, "exp": exp}
    for i in range(0, len(values), 5000):
        client.push([{"stream": labels, "values": values[i:i + 5000]}])
    return len(values)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
