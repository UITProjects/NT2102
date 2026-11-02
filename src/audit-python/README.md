# auditship: Linux auditd → Loki (ubuntu-etw)

The Linux counterpart of the Windows collector's shipper (`src/etw-python`). It runs on the `ubuntu-etw` endpoint
and ships every auditd record to Loki on `ubuntu-trainer`. **Raw logs only:** the OCR-APT pipeline reads
`job="silketw"` (Windows ETW) and has no converter or model for these records (DEVIATIONS D11).

```
kernel audit rules (role linux_auditd: exec, net, file watches; the auditship user excluded)
  -> auditd -> /var/log/audit/audit.log           one record per line, ENRICHED format
  -> auditship run (systemd, user auditship)       tail from saved {inode, offset}, follow rotation
  -> POST {loki_url}/loki/api/v1/push              labels {job="auditd", host="ubuntu-etw"}
```

## One record → one Loki entry

One `curl example.com` produces several records sharing one event serial (`4567`):

```
type=SYSCALL msg=audit(1762085657.123:4567): arch=c000003e syscall=59 success=yes ... exe="/usr/bin/curl" key="exec"␝ARCH=x86_64 SYSCALL=execve UID="it2102"
type=EXECVE  msg=audit(1762085657.123:4567): argc=2 a0="curl" a1="example.com"
```

| Line part | Becomes |
|---|---|
| `1762085657.123` (epoch seconds.ms) | entry timestamp `1762085657123000000` ns (event time, not read time) |
| `type=SYSCALL` | structured metadata `type` |
| `key="exec"` (the rule's `-k` tag; SYSCALL records only) | structured metadata `key` |
| the whole line, unchanged (incl. `␝` = `\x1d`, which splits raw fields from resolved names) | entry line |

Labels stay `{job, host}` (low cardinality); filter in Grafana with e.g.
`{job="auditd", host="ubuntu-etw"} | key="exec"` or `| type="EXECVE"`.

## Delivery (`tail.py`)

- `positions.json` = `{"inode", "offset"}`, rewritten after every batch Loki accepted (204). Loki down = the
  offset stays and the batch is retried with backoff (1 s → 30 s), so lines are delayed, not lost.
- Rotation: auditd renames `audit.log` → `audit.log.1`. The inode changes under the same path, so we finish the
  old file (found by its inode) from the saved offset, then start the new file at 0.
- Batches of ≤ 1 MiB of whole lines; a half-written last line waits. Loki refusing a batch for good (4xx, e.g.
  older than the 3-day window) skips it and counts it in `rejected_batches`.
- The Loki client (`LokiClient`, `LokiError`) is imported from `etwcollect.loki` (uv path dependency), so both
  collectors share one push contract.

## Feedback loop (why the auditship user is excluded)

Each push opens a TCP connection to Loki and rewrites `positions.json`. If the audit rules saw that, every
shipped batch would create new records to ship (the loop SilkService had on Windows, D8/D10). The first
rule in `/etc/audit/rules.d/nt2102.rules` is therefore `-a never,exit -F uid=<auditship uid>`. auditd itself is
never audited by the kernel.

## Commands

```bash
auditship run --loki-url http://10.21.3.20:3100 --host ubuntu-etw   # what the systemd unit runs
auditship status                                                    # /var/lib/auditship/status.json + age
auditship selftest                                                  # temp log, fake rotation, stub Loki
```

`selftest` proves "it runs" (parsing, partial lines, Loki outage, rotation, restart). It says nothing about
detecting anything.
