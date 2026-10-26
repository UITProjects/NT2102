"""collector.py - the running collector: ETW callback + writer, shipper and status threads.

    thread "etw"     session.consume(): Windows calls _on_event() for every event, one at a time
    thread "flush"   writer.flush() every second (whole lines, one write per file)
    thread "ship"    shipper.step() every second: new whole lines -> Loki (skipped with --no-push)
    thread "status"  collector-status.json every 5 s (counters, lag, ETW lost-event counters, backlog)
    main thread      waits; `etwcollect stop` (another process) stops the session -> "etw" returns ->
                     final flush, shipper drains what is left (bounded), status "stopped", exit

_on_event() per event (cheap first, so excluded events cost little):
    1. which provider? (header GUID)            unknown -> count, return
    2. kproc from an excluded PID?              drop before decoding (rule a)
    3. schema (cached) + decode the payload
    4. kfile rules (a)+(b) / knet rule (c)      drop our own I/O
    5. ProcessStart -> remember the child's name (and exclude it if it is Alloy, during the switch)
    6. build the SilkETW-format line, hand it to the writer
"""
from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from . import dosmap, record
from .decode import DecodeError, FILETIME_UNIX
from .filters import Excluder
from .loki import LokiClient, Shipper, utc_now_iso
from .procnames import ProcNames, query_image
from .providers import Provider
from .writer import LineWriter

SESSION = "etwlab-collector"


@dataclass
class Config:
    out_dir: str                      # raw_root\<split>\<run>
    split: str
    run: str
    providers: list[Provider]
    raw_root: str
    status_path: str
    positions_path: str
    host: str = "ws-etw"
    loki_url: str | None = None       # None or push=False: write files only
    push: bool = True
    session: str = SESSION
    exclude_images: tuple = ("alloy",)
    drain_s: float = 30.0
    extra_own_paths: tuple = field(default_factory=tuple)


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _now_filetime() -> int:
    return time.time_ns() // 100 + FILETIME_UNIX


class Collector:
    def __init__(self, cfg: Config, log=print):
        from . import winapi as w                      # Windows only from here on
        from .schema import Schemas
        from .session import RealtimeSession
        self.w, self.cfg, self.log = w, cfg, log
        self.own_pid = os.getpid()
        self.by_guid = {p.guid_le: p for p in cfg.providers}
        self.dmap = dosmap.dos_device_map()
        own_paths = [dosmap.to_device(cfg.raw_root, self.dmap).rstrip("\\") + "\\",
                     dosmap.to_device(cfg.out_dir, self.dmap).rstrip("\\") + "\\",
                     dosmap.to_device(cfg.status_path, self.dmap),
                     dosmap.to_device(cfg.positions_path, self.dmap),
                     # ETW's own spill file for this real-time session (System writes it): ETW plumbing, not activity
                     dosmap.to_device(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "LogFiles",
                                                   "WMI", "RtBackup", f"EtwRT{cfg.session}.etl"), self.dmap),
                     *(dosmap.to_device(p, self.dmap) for p in cfg.extra_own_paths)]
        self.ex = Excluder({self.own_pid}, cfg.exclude_images, own_paths)
        self.names = ProcNames()
        self.schemas = Schemas({p.guid_le: p.name for p in cfg.providers})
        self.writer = LineWriter(cfg.out_dir, [p.stem for p in cfg.providers])
        self.client = LokiClient(cfg.loki_url) if (cfg.push and cfg.loki_url) else None
        self.shipper = Shipper(self.client, cfg.positions_path, cfg.host, log=log) if self.client else None
        self.session = RealtimeSession(cfg.session)
        self.stats = {p.stem: Counter() for p in cfg.providers}
        self.newest = {p.stem: 0 for p in cfg.providers}   # newest event FILETIME written, per provider
        self.other = 0
        self.decode_errors = 0
        self.slow_events = 0
        self.callback_errors = 0
        self.last_error = None
        self.state = "starting"
        self.started_utc = utc_now_iso()
        self.etw_rc = None
        self._stop = threading.Event()

    # ---------------------------------------------------------------- per event (ETW thread)
    def _callback(self, rp) -> None:
        try:
            self._on_event(rp)
        except Exception as e:                       # never let an exception cross back into Windows
            self.callback_errors += 1
            self.last_error = f"{type(e).__name__}: {e}"

    def _on_event(self, rp) -> None:
        r = rp.contents
        h = r.EventHeader
        prov = self.by_guid.get(bytes(h.ProviderId))
        if prov is None:                             # e.g. the session's own EventTrace header event
            self.other += 1
            return
        stem, pid = prov.stem, h.ProcessId
        st = self.stats[stem]
        st["events"] += 1
        plan = self.schemas.get(rp)                  # cached: a dict lookup after the first event of a type
        start = stem == "kproc" and plan.event_name.startswith("ProcessStart")
        # A ProcessStart is logged in the PARENT's context, so one we excluded (a child we spawned) is still
        # decoded first: the PID -> name map must learn the child either way.
        if stem == "kproc" and not start and self.ex.header(stem, pid):
            return
        ptr = 4 if h.Flags & self.w.EVENT_HEADER_FLAG_32_BIT_HEADER else 8
        n = r.UserDataLength
        data = ctypes.string_at(r.UserData, n) if n else b""
        try:
            props, slow = plan.decode(rp, data, ptr)
        except DecodeError as e:
            self.decode_errors += 1
            self.last_error = f"decode {plan.provider_name}/{plan.event_name}: {e}"
            props, slow = {}, False
        self.slow_events += slow
        if stem == "kfile":
            if self.ex.kfile(plan.event_name, pid, props):
                return
        elif stem == "knet":
            if self.ex.knet(props):
                return
        elif start:
            child = _int(props.get("ProcessID"))
            if child is not None:
                name = self.names.set(child, props.get("ImageName", "")) or self.names.set(child, query_image(child))
                self.ex.note_process(child, name)
            if self.ex.header(stem, pid):
                return
        hdr = {"ts": h.TimeStamp, "tid": h.ThreadId, "pid": pid, "pname": self.names.get(pid), "ptr": ptr, "len": n}
        self.writer.add(stem, record.line(record.build(plan, hdr, props)))
        st["written"] += 1
        if h.TimeStamp > self.newest[stem]:
            self.newest[stem] = h.TimeStamp

    # ---------------------------------------------------------------- helper threads
    def _every(self, seconds: float, fn) -> None:
        while not self._stop.wait(seconds):
            try:
                fn()
            except Exception as e:                   # a full disk or a bad status write must not kill capture
                self.last_error = f"{fn.__name__}: {type(e).__name__}: {e}"
                self.log(f"[collector] {self.last_error}")

    def _ship(self) -> None:
        self.shipper.push_meta(self.cfg.split, self.cfg.run, self.dmap)
        self.shipper.step()

    def status(self) -> dict:
        now = _now_filetime()
        provs = {}
        for p in self.cfg.providers:
            nf = self.newest[p.stem]
            provs[p.stem] = {**self.stats[p.stem], "lines": self.writer.lines[p.stem], "bytes": self.writer.bytes[p.stem],
                             "newest_event_utc": record.filetime_iso(nf) if nf else None,
                             "lag_s": round((now - nf) / 1e7, 1) if nf else None}
        ship = None
        if self.shipper:
            ship = {"loki": self.cfg.loki_url, "pushed_lines": self.shipper.pushed_lines,
                    "bad_lines": self.shipper.bad_lines, "rejected_batches": self.shipper.rejected,
                    "last_push_ns": self.shipper.last_push_ns, "last_error": self.shipper.last_error,
                    "backlog_bytes": {os.path.basename(k): v for k, v in self.shipper.backlog().items()}}
        return {"state": self.state, "pid": self.own_pid, "session": self.cfg.session, "split": self.cfg.split,
                "run": self.cfg.run, "dir": self.cfg.out_dir, "started_utc": self.started_utc,
                "updated_utc": utc_now_iso(), "providers": provs, "excluded": dict(self.ex.counts),
                "excluded_pids": sorted(self.ex.pids), "other_events": self.other,
                "decode_errors": self.decode_errors, "slow_events": self.slow_events,
                "schemas": len(self.schemas.cache), "no_schema": self.schemas.no_schema,
                "callback_errors": self.callback_errors, "last_error": self.last_error, "etw_rc": self.etw_rc,
                "etw": self.session.query() if self.state == "running" else None, "ship": ship}

    def write_status(self) -> None:
        tmp = self.cfg.status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.status(), fh, indent=1)
        os.replace(tmp, self.cfg.status_path)

    # ---------------------------------------------------------------- lifecycle
    def run(self) -> int:
        for pid, name in self.names.snapshot():
            self.ex.note_process(pid, name)
        meta = os.path.join(self.cfg.out_dir, "dosmap.meta")
        if not os.path.exists(meta):
            dosmap.write_meta(meta, self.dmap)
        if self.session.start():
            self.log(f"[collector] stopped a stale session {self.cfg.session!r} first")
        for p in self.cfg.providers:
            self.session.enable(p.guid, p.keywords)
        if self.shipper:
            for p in self.cfg.providers:
                self.shipper.track(self.writer.paths[p.stem])
        self.state = "running"
        self.log(f"[collector] pid {self.own_pid} capturing {self.cfg.split}/{self.cfg.run} -> {self.cfg.out_dir}"
                 f" ({', '.join(p.stem for p in self.cfg.providers)}; push={'on' if self.shipper else 'off'})")
        threads = [threading.Thread(target=self._every, args=(1.0, self.writer.flush), name="flush", daemon=True),
                   threading.Thread(target=self._every, args=(5.0, self.write_status), name="status", daemon=True)]
        if self.shipper:
            threads.append(threading.Thread(target=self._every, args=(1.0, self._ship), name="ship", daemon=True))
        for t in threads:
            t.start()
        etw = threading.Thread(target=self._consume, name="etw", daemon=True)
        etw.start()
        try:
            while etw.is_alive():
                etw.join(0.5)
        except KeyboardInterrupt:                    # console run: Ctrl+C = stop
            self.session.stop()
            etw.join(10)
        self.state = "draining"
        self._stop.set()
        for t in threads:
            t.join(5)
        self.writer.close()
        ok = True
        if self.shipper:
            self.shipper.push_meta(self.cfg.split, self.cfg.run, self.dmap)
            self.shipper.close_run(self.cfg.out_dir)
            ok = self.shipper.drain(self.cfg.drain_s)
            if not ok:
                self.log(f"[collector] Loki backlog left after {self.cfg.drain_s:.0f} s; shipped at the next start")
        self.state = "stopped"
        self.write_status()
        self.log(f"[collector] stopped (ETW status {self.etw_rc}); lines " +
                 ", ".join(f"{s}={self.writer.lines[s]}" for s in self.writer.lines))
        return 0

    def _consume(self) -> None:
        try:
            self.etw_rc = self.session.consume(self._callback)
        except OSError as e:
            self.last_error = str(e)
            self.log(f"[collector] consumer failed: {e}")
