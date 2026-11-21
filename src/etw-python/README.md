# src/etw-python/: ETW collector (`etwcollect`)

The lab's own Windows kernel-event collector, replacing SilkETW + Grafana Alloy (DEVIATIONS D10). It records
Kernel-Process, Kernel-File and Kernel-Network, writes SilkETW-format JSON lines and pushes them to Loki itself.
Python stdlib + ctypes only, no third-party packages.

## Outcome

- Same files and Loki labels as before, so nothing on the trainer changed.
- Fixes the feedback loop that broke SilkService: its own file writes fed Kernel-File faster than it could drain them.
  `etwcollect` drops its own I/O in the ETW callback and batches one write per file per second.
- Plain values (`4096`, dotted IPv4, host-order ports), UTC timestamps and far fewer `N/A` process names.

## Architecture

```
ETW real-time session   Kernel-Process · Kernel-File · Kernel-Network (+ Sysmon EID 1, optional)
  ─► callback thread    drop own I/O, decode payload (cached TDH schema)
  ─► run folder         kproc.json · kfile.json · knet.json · dosmap.meta   (one batched write per second)
  ─► shipper thread     new whole lines from saved offsets ─► Loki /push  {job, host, split, run, provider}
```

Runs as the SYSTEM scheduled task `etwlab-collector`, installed by Ansible (`win_collector`).

## Key files (`src/etwcollect/`)

| file | what |
|---|---|
| `session.py` · `winapi.py` | ETW real-time session over ctypes |
| `schema.py` · `decode.py` · `record.py` | TDH schema cache, payload decode, SilkETW-format line |
| `filters.py` · `procnames.py` · `dosmap.py` | self-exclusion, PID → name, device-path map |
| `writer.py` · `loki.py` | batched file writer, Loki shipper with offsets and backoff |
| `collector.py` · `cli.py` · `selftest.py` | wiring, the `etwcollect` command, offline + live selftests |
