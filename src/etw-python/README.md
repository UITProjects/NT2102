# etw-python: the lab's ETW collector (`etwcollect`)

This replaces SilkETW/SilkService and Grafana Alloy on the Windows VM (DEVIATIONS D10). It records the same
three kernel providers into the same files and pushes them to Loki with the same labels, so nothing on the
trainer changes. It exists because SilkService wrote every event with its own open + write + close, and
those writes fed back into Kernel-File faster than it could drain them (`docs/notes/silkservice-kfile-lag.md`).

```
ETW real-time session "etwlab-collector"   Kernel-Process 0x50 · Kernel-File 0x380 · Kernel-Network 0x30
  -> callback (one Python thread)          drop our own I/O first, decode the payload (cached TDH schema)
  -> C:\lab-out\etw\raw\<split>\<run>\     kproc.json · kfile.json · knet.json · dosmap.meta
       one SilkETW-format JSON line per event, one batched write per file per second
  -> shipper thread                        whole new lines from saved offsets -> Loki /loki/api/v1/push
       {job="silketw", host, split, run, provider} + metadata {event, pid, process}, ts = event time
```

stdlib + ctypes only, no third-party packages.

## Commands

All commands run elevated on the VM. `etwctl` and the SYSTEM task call them for you.

| command | what it does |
|---|---|
| `etwcollect run` | capture into the run folder named in `C:\ProgramData\etwlab\state.json` (written by `etwctl start`) until stopped. `--no-push` = files only; `--out-dir DIR` = another folder |
| `etwcollect stop` | stop the ETW session; waits until the collector has flushed, shipped what it can and written `state: stopped` |
| `etwcollect status` | `collector-status.json` (lines, lag, excluded counts, ETW lost-event counters, Loki backlog) + whether the session runs |
| `etwcollect dosmap [--out F]` | this machine's `\device\harddiskvolumeN -> c:` map |
| `etwcollect push-ledger --exp ID --dir <exp>\labels` | the 07 workload ledger CSVs -> Loki (`job="workload-ledger"`) |
| `etwcollect selftest` | pure parts on any OS (decode, filters, writer, shipper vs a fake Loki, converter round-trip) |
| `etwcollect selftest --live` | Windows, elevated: a real 15 s session; a child process + a canary file must show up and nothing from our own PID |

Settings come from `C:\ProgramData\etwlab\etwlab.json` (or `$ETWLAB_CONFIG`): `Collectors`, `RawRoot`,
`LokiUrl`, `Host`. It is written by the `win_collector` Ansible role.

## Where the code runs

- **Local lab:** the Vagrant box mounts this folder at `C:\etw-python`. Ansible runs
  `uv sync --frozen` with the venv on the VM's own disk (`UV_PROJECT_ENVIRONMENT=C:\Tools\etw-python-venv`).
  A venv on the VirtualBox share would break and would also clash with a host `.venv`. A code change on
  the host is live after the next `etwctl stop` / `etwctl start`.
- **Cloud:** Ansible clones the repo, and the source is `C:\lab\NT2102\src\etw-python`.
- The SYSTEM scheduled task `etwlab-collector` runs `<venv>\Scripts\python.exe -m etwcollect run`.
  `etwctl start` / `stop` start and stop it.

On the host (Linux), only the selftest runs:

```
cd src/etw-python && uv run etwcollect selftest
```

## Modules (`src/etwcollect/`)

| file | role |
|---|---|
| `winapi.py` | ctypes declarations of the ETW / TDH / process structs and functions (byte sizes in comments) |
| `session.py` | one real-time session: start (stopping a stale one), enable providers, consume, query, stop |
| `schema.py` | TDH: event type -> names + field list, cached; slow path through TDH for unusual fields |
| `decode.py` | pure Python: payload bytes + field list -> `{name: text}` (plain ints, 0x.. pointers, dotted IPv4, host-order ports) |
| `record.py` | the SilkETW-format line (`EventName` = task or task/opcode, UTC timestamp with 7 digits) |
| `filters.py` | the three exclusion rules (own PID; anything on our own files; network events by payload PID) |
| `procnames.py` | PID -> process name (startup snapshot + ProcessStart), so short-lived processes keep their name |
| `writer.py` | per-file batches, one write + flush per second, whole lines only |
| `loki.py` | push client, file-tail shipper with saved offsets and backoff, dos-map line, ledger push |
| `dosmap.py` | `QueryDosDeviceW` map, device-path conversion, `dosmap.meta` |
| `collector.py` | wires it together: the ETW callback + flush, ship and status threads, clean stop |
| `cli.py`, `__main__.py` | the `etwcollect` command |
| `selftest.py` | offline + `--live` checks |
| `tools/parity.py` | compares a capture with a SilkService capture of the same minutes (migration check) |

## Differences from SilkETW lines (DEVIATIONS D10)

- `XmlEventData` holds only the payload fields. There is no `FormattedMessage` and none of TraceEvent's
  header copies (`MSec`, `PName`, `TID`, header `PID`).
- Values are plain: `4096` not `4,096`, and `23.220.64.154` / `443` instead of SilkETW's signed integers.
- On Kernel-Network, `PID` is the payload owner. SilkETW put it in `_PID` and the converter read the wrong
  field (DEVIATIONS D8).
- `TimeStamp` is UTC (`+00:00`) with 7 fractional digits, not the VM's `+07:00`.
- `ProcessName` comes from the startup snapshot plus ProcessStart events, so it is `N/A` far less often.

Not verified yet: whether Server 2022's ProcessStart payload carries `ImageName`. If it does not, the
child's name comes from `QueryFullProcessImageNameW`. The parity run settles it.
