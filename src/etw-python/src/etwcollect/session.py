"""session.py - one real-time ETW session: start it, enable the providers, consume events, query, stop.

ETW in four calls (all in advapi32.dll):
    StartTraceW(name, props)        create a session = a set of kernel buffers events are written into
    EnableTraceEx2(session, guid)   tell a provider (e.g. Kernel-File) to write into it, filtered by keywords
    OpenTraceW + ProcessTrace       attach a consumer: ProcessTrace calls our callback once per event and
                                    blocks until the session is stopped
    ControlTraceW(name, STOP)       from any process: stop the session; ProcessTrace then returns

A session is named and outlives the process that started it: if the collector crashes, the session keeps
buffering. start() therefore stops an old session of the same name first.

Buffers: BufferSize 256 KB x up to 512 buffers = up to 128 MB of kernel memory to absorb bursts while the
Python callback is busy. If they are all full, ETW drops events and counts them (EventsLost /
RealTimeBuffersLost, see query()); SilkService lost time instead of events because it fell behind itself.
"""
from __future__ import annotations

import ctypes

from . import winapi as w


class RealtimeSession:
    def __init__(self, name: str, buffer_kb: int = 256, min_buffers: int = 64, max_buffers: int = 512,
                 flush_s: int = 1):
        self.name = name
        self.buffer_kb, self.min_buffers, self.max_buffers, self.flush_s = buffer_kb, min_buffers, max_buffers, flush_s
        self.handle = w.TRACEHANDLE(0)
        self.trace = w.TRACEHANDLE(w.INVALID_PROCESSTRACE_HANDLE)
        self._cb = None                          # the ctypes callback object: must stay referenced while consuming

    def _props(self) -> tuple[ctypes.Array, "ctypes._Pointer"]:
        """EVENT_TRACE_PROPERTIES followed by room for the session name, in one buffer (the API wants that)."""
        size = ctypes.sizeof(w.EVENT_TRACE_PROPERTIES) + 2 * (len(self.name) + 1) + 2048
        buf = ctypes.create_string_buffer(size)
        p = ctypes.cast(buf, w.PPROPS)
        p.contents.Wnode.BufferSize = size
        p.contents.Wnode.Flags = w.WNODE_FLAG_TRACED_GUID
        p.contents.LoggerNameOffset = ctypes.sizeof(w.EVENT_TRACE_PROPERTIES)
        return buf, p

    @staticmethod
    def stop_by_name(name: str) -> dict | None:
        """Stop a session by name from any process. Returns its final counters, None if it was not running."""
        s = RealtimeSession(name)
        buf, p = s._props()
        rc = w.ControlTraceW(0, name, p, w.EVENT_TRACE_CONTROL_STOP)
        if rc == w.ERROR_WMI_INSTANCE_NOT_FOUND:
            return None
        w.check(rc, f"ControlTraceW(STOP {name})")
        return s._counters(p)

    def start(self) -> bool:
        """Create the session. Returns True if an old session of the same name had to be stopped first."""
        stale = False
        for attempt in range(2):
            buf, p = self._props()
            p.contents.Wnode.ClientContext = 1                 # timestamps from QPC, delivered as FILETIME
            p.contents.LogFileMode = w.EVENT_TRACE_REAL_TIME_MODE
            p.contents.BufferSize = self.buffer_kb
            p.contents.MinimumBuffers = self.min_buffers
            p.contents.MaximumBuffers = self.max_buffers
            p.contents.FlushTimer = self.flush_s
            rc = w.StartTraceW(ctypes.byref(self.handle), self.name, p)
            if rc == w.ERROR_ALREADY_EXISTS and attempt == 0:
                self.stop_by_name(self.name)
                stale = True
                continue
            w.check(rc, f"StartTraceW({self.name})")
            return stale
        return stale

    def enable(self, guid, keywords: int, level: int = w.TRACE_LEVEL_VERBOSE) -> None:
        g = w.GUID.from_uuid(guid)
        rc = w.EnableTraceEx2(self.handle, ctypes.byref(g), w.EVENT_CONTROL_CODE_ENABLE_PROVIDER, level,
                              keywords, 0, 0, None)
        w.check(rc, f"EnableTraceEx2({guid}, keywords={keywords:#x})")

    def consume(self, callback) -> int:
        """Blocks: callback(POINTER(EVENT_RECORD)) runs on THIS thread for every event until the session
        stops. Returns ProcessTrace's status (0 or ERROR_CANCELLED after a normal stop)."""
        lf = w.EVENT_TRACE_LOGFILEW()
        lf.LoggerName = self.name
        lf.ProcessTraceMode = w.PROCESS_TRACE_MODE_REAL_TIME | w.PROCESS_TRACE_MODE_EVENT_RECORD
        self._cb = w.EVENT_RECORD_CALLBACK(callback)
        lf.EventRecordCallback = self._cb
        self.trace = w.TRACEHANDLE(w.OpenTraceW(ctypes.byref(lf)))
        if self.trace.value == w.INVALID_PROCESSTRACE_HANDLE:
            raise OSError(ctypes.get_last_error(), f"OpenTraceW({self.name}) failed")
        try:
            return w.ProcessTrace(ctypes.byref(self.trace), 1, None, None)
        finally:
            w.CloseTrace(self.trace)

    @staticmethod
    def _counters(p) -> dict:
        c = p.contents
        return {"events_lost": int(c.EventsLost), "realtime_buffers_lost": int(c.RealTimeBuffersLost),
                "log_buffers_lost": int(c.LogBuffersLost), "buffers_written": int(c.BuffersWritten),
                "buffers": int(c.NumberOfBuffers), "free_buffers": int(c.FreeBuffers)}

    def query(self) -> dict | None:
        buf, p = self._props()
        rc = w.ControlTraceW(0, self.name, p, w.EVENT_TRACE_CONTROL_QUERY)
        if rc != 0:
            return None
        return self._counters(p)

    def stop(self) -> dict | None:
        return self.stop_by_name(self.name)
