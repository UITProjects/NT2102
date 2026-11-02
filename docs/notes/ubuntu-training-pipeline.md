# Design: training pipeline for the Linux endpoint (ubuntu-etw)

Status: **design only (2026-11-02), nothing built.** Input is the auditd stream that already reaches Loki
(`{job="auditd", host="ubuntu-etw"}`, DEVIATIONS D11). Goal: train an idle model for `ubuntu-etw` with the same
OCR-APT core as the Windows host (one-class RGCN per node type over behavior features), then score windows.

## 1. What already exists and what the Linux side changes

```
                Windows (exists)                          Linux (this design)
collect     etwcollect -> Loki job="silketw"          auditd -> auditship -> Loki job="auditd"        DONE (D11)
pull        loki_pull.py (by run or time range)       loki_pull.py --job auditd --host ubuntu-etw     CHANGE
convert     etw2nodlink.py -> NODLINK CSV             audit2nodlink.py -> NODLINK CSV                 NEW
window      etwwindow.py                              etwwindow.py (unchanged)                        REUSE
check       check_rdf_input.py                        check_rdf_input.py (ns timestamps)              SMALL
features    encode_to_features.py (6 actions, ms)     same, action list + ts unit per graph           CHANGE
train       train_gnn_models.py                       same (feature width from the scaler)            SMALL
score/live  score_window.py, src/live/                later phase (section 7)                         LATER
```

Everything after the converter works on the NODLINK edge CSV
(`source-id,source-type,destination-id,destination-type,edge-type,timestamp`), so most of the work is one
new converter plus removing two Windows constants.

## 2. Target schema: NODLINK `SimulatedUbuntu` (verified on disk)

[AUTHOR CODE] `OCR-APT/src/database_config.py` (`order_x_features`) already defines a Linux NODLINK host,
`SimulatedUbuntu`, and the authors' data + trained models are in `dataset/nodlink/SimulatedUbuntu/experiments/`.
Measured from `SimulatedUbuntu_train_edges.csv` (2026-11-02):

| Property | SimulatedW10 (what we use now) | SimulatedUbuntu |
|---|---|---|
| Node types | PROCESS, FILE, NET | PROCESS, FILE, NET (same) |
| Edge types | 6 abstract: Write, Read, Send, Recv, Start, Load | 17 syscall names: `execve rmdir chmod rename write writev read readv sendto send recvfrom sendmsg recvmsg clone fork pipe fcntl` |
| Features | 6×2 + 3 idle = **15** | 17×2 + 3 idle = **37** |
| Timestamp | epoch **ms** (13 digits) | epoch **ns** (19 digits, e.g. `1648196413587579615`) |
| Direction | process → object | process → object for every type, including `read`/`recvmsg` |
| Volume (train, 58 h) | n/a | `read` 11.25 M, `write` 0.89 M, then `rename`, `writev`, `recvmsg`, `fcntl`, `clone` ~12–21 k each, `execve` 6.4 k |

[AUTHOR CODE] `encode_to_PyG.py` l.164–170 divides idle gaps by 1000 only for W10/WS12, and by 10⁹ for
everything else, so ns timestamps are expected for SimulatedUbuntu.

Caveat [INFERENCE]: in `SimulatedUbuntu_train_node_attrs.csv` some socket nodes (`ip:port->ip:port`) are typed
`FILE` while the edges type them `NET`: an upstream parsing inconsistency (paper §7 warns about dataset
parsing issues). We type sockets `NET` everywhere.

**Recommendation:** graph name `SimulatedUbuntu`, the authors' 17 edge types in their column order, epoch ns.
That keeps the Linux model as close to [AUTHOR CODE] as the W10 one is. Two [OURS] additions: `connect` and
`accept` as extra columns (auditd's natural network events; the NODLINK vocabulary has none), giving
19×2 + 3 = **41** features. Columns our rules never produce stay 0.

## 3. The new converter: `src/pipeline/audit2nodlink.py`

### 3.1 auditd records → one event

One syscall writes several lines (records) that share a serial number. They must be **grouped by serial, not by
line order**: Loki returns entries sorted by timestamp, and all records of one event have the identical timestamp,
so their order within it isn't guaranteed. `EOE` (end of event) closes a group.

Worked example 1, `curl example.com` started by bash (pid 4100):

```
type=SYSCALL  msg=audit(1793622413.543:804): syscall=59 success=yes pid=4101 ppid=4100 comm="curl" exe="/usr/bin/curl" key="exec"
type=EXECVE   msg=audit(1793622413.543:804): argc=2 a0="curl" a1="example.com"
type=PATH     msg=audit(1793622413.543:804): item=0 name="/usr/bin/curl" ...
type=EOE      msg=audit(1793622413.543:804):
```

→ serial 804, syscall 59 = `execve`, time 1793622413.543 s = **1793622413543000000 ns** →
one edge `PROCESS(bash 4100) -execve-> PROCESS(curl 4101)`.

Worked example 2, curl then connects (syscall 42 = `connect`) to 93.184.215.14:80:

```
type=SYSCALL  msg=audit(1793622413.608:810): syscall=42 success=yes pid=4101 exe="/usr/bin/curl" key="net"
type=SOCKADDR msg=audit(1793622413.608:810): saddr=020000505DB8D70E0000000000000000
```

`saddr` is the raw `sockaddr_in` in hex: `0200` = family 2 (IPv4), `0050` = port 0x50 = **80**,
`5DB8D70E` = 0x5D.0xB8.0xD7.0x0E = **93.184.215.14** → edge `PROCESS(curl 4101) -connect-> NET(93.184.215.14:80)`.

### 3.2 Mapping table (syscall → edge type)

| auditd syscall (rule needed) | Edge | Object node | Note |
|---|---|---|---|
| `execve`, `execveat` (have) | `execve` | PROCESS (child) | parent = `ppid` |
| `clone`, `clone3`, `fork`, `vfork` (add) | `clone` / `fork` | PROCESS (child pid = `exit=`) | |
| `openat` read-only (add) | `read` | FILE | [OURS] an *open* for reading, not each `read()` call (see 3.4) |
| `openat` with write/create flags, watch `-p w` hits (have, partly) | `write` | FILE | same caveat |
| `rename`, `renameat2` (add) | `rename` | FILE (new path) | |
| `unlink(at)`, `rmdir` (add) | `rmdir` | FILE | NODLINK has only `rmdir`; [OURS] unlink folded in |
| `chmod`, `fchmodat` (add) | `chmod` | FILE | |
| `connect` (have) | `connect` [OURS] | NET `ip:port` | |
| `accept`, `accept4` (have) | `accept` [OURS] | NET `ip:port` | |

Failed syscalls (`success=no`) are dropped: in example 2's capture, curl's first `connect`s fail with `exit=-2`
(ENOENT, the nscd socket), so they aren't real connections.

### 3.3 Node identity and attributes

Same rules as Windows (CLAUDE.md §9):
- **PROCESS** key = hash(`pid`, time of the `execve`/`clone` that created it). auditd has no process start time,
  so a process already running when the capture began is keyed by its first event in the capture. PIDs get reused,
  so a bare PID would merge unrelated processes and corrupt idle-time features. Attribute: `exe` (or `comm`).
- **FILE** key = hash(absolute path). Relative `PATH name=` values are joined with the event's `CWD` record.
  Attribute: the path.
- **NET** key = hash(`ip:port`) from `SOCKADDR`; IPv6 (family 10) decoded too; Unix sockets (family 1) dropped
  or typed FILE (decide on real data). Attribute: `ip:port`.

### 3.4 Biggest semantic gap: per-call vs per-open counts

NODLINK counted every `read()`/`write()` call: 11.25 M reads in 58 h. Auditing each `read()` with auditd would
flood the VM. So we count **opens**, not calls. The feature step L2-normalises the action counts per node (each
node's count vector is divided by its length), so the absolute scale mostly cancels out, but the *ratios* between
actions differ from NODLINK's.

Example with real numbers: a log-tailer opens `/var/log/syslog` once and calls `read()` 500 times.
- NODLINK-style vector `(read 500, write 0)` → L2 length √(500²) = 500 → normalised `(1.0, 0.0)`.
- Ours `(read 1, write 0)` → `(1.0, 0.0)`: **same**.

But a process that opens 1 file for read and writes 1 file 100 times:
- NODLINK-style `(read 1, write 100)` → length √(1² + 100²) ≈ 100.005 → `(0.01, 0.99995)`.
- Ours `(read 1, write 1)` → length √2 ≈ 1.414 → `(0.707, 0.707)`.

**Consequence:** a model trained on our Linux data must never be compared with, or mixed with, NODLINK
SimulatedUbuntu. Our model is trained and tested on our own captures only.

## 4. Changes to existing stages

- `audit rules` (role `linux_auditd`, `nt2102.rules.j2`): add the syscalls marked "add" in 3.2. `openat` is the
  noisy one. Scope it to the home/system dirs we care about (`-F dir=`), exclude `/proc` and `/sys`, keep the
  shipper exclusion first, then measure the idle rate. It is ~12–55 lines/30 s today; expect 10–100× more.
- `loki_pull.py`: `--job auditd --host ubuntu-etw` and a time range. The auditd stream has no `split`/`run`
  labels, so the pull writes `raw/<split>/range-<start>/audit.log` with the run name taken from the range.
- `encode_to_features.py`: `ACTIONS` and `ts_unit` chosen per graph (`SimulatedW10` → 6 actions/ms,
  `SimulatedUbuntu` → 19 actions/ns), and both recorded in `feature_scaler.json`, which already stores
  `feature_order` and `ts_unit`.
- `score_window.py`: the hardcoded `15` becomes `len(scaler["feature_order"])`; `train_gnn_models.py` already
  reads the width from the features file.
- `build_experiment.py` / `lab` CLI: a `--graph SimulatedUbuntu` choice that picks `audit2nodlink.py` as the
  converter. `lab pull/loop` get `--host ubuntu-etw`. Run folders record the graph in `experiment.json`, so a
  Linux model can't be promoted to the Windows live server by mistake (`lab promote` checks it).

## 5. Training data: idle, same rule as Windows (D2)

Benign = an idle time range of `ubuntu-etw` pulled from Loki. Provisioning (apt, Ansible runs) is **not** idle.
Ansible writes under `/home/it2102/.ansible`, which the `/home` watch records. Same risk as the Windows idle
model, and stronger: idle Ubuntu is very quiet (cron, systemd timers, `unattended-upgrades`, snapd), so the model
is thin and any real activity may go red. Pull at least several hours, including one daily-timer cycle
(`apt-daily`, `logrotate`), so routine maintenance is in the benign set.

## 6. Build order (each stage leaves CSV/JSON to check, each script has `--selftest`)

1. **Rules**: extend `nt2102.rules.j2`, re-run `target.yml`, measure the idle line rate, and confirm the shipper
   still never appears.
2. **Converter**: `audit2nodlink.py` with a selftest built from real captured lines (the two examples above).
   EDA: edge-type counts and the direction check, the same `awk` as the W10 note in `etw2nodlink.py`.
3. **Pull + build**: `loki_pull.py --job auditd`, `build_experiment.py --graph SimulatedUbuntu` →
   `windows.csv`, `check.json`.
4. **Features**: per-graph action list + ns; EDA notebook `06-features-ubuntu` (which columns are always 0).
5. **Train**: `lab loop --graph SimulatedUbuntu ...` → run folder with `lineage.json`.
6. **Score held-out idle windows**: they should stay below the threshold. That proves "it runs" plus a benign
   sanity check, not detection.

## 7. Out of scope here (later phases)

- **Live Linux detection**: `loki_source.py` follows `job="silketw"` with run labels. A Linux live source
  needs the auditd stream, event-serial grouping across poll boundaries, and the new converter in `engine.py`.
- **A Linux attack**: there's no Linux target yet. A red window needs an attack on `ubuntu-etw`
  (e.g. a vulnerable web app → RCE, mirroring DVWA), labelled the same way as the Windows attack pack.
- **Alg. 1 / Alg. 2 on Linux**: work on any NODLINK graph, but the LLM prompt's node attributes
  (`exe`, path, `ip:port`) should be checked on real output.

## 8. Decisions for the user (recommendation first)

1. Edge vocabulary: **SimulatedUbuntu's 17 + `connect`/`accept` (41 features)**, or map to W10's 6 abstract
   types (15 features, reuses everything unchanged, but drops the [AUTHOR CODE] Linux config).
2. `openat` scope: **scoped to chosen dirs**, or all files (full read coverage, much more volume).
3. Where the Linux model lives: **separate run folders tagged `SimulatedUbuntu`**, one model per OS. Never one
   model for both: the behavior distributions differ.

Every choice above becomes a DEVIATIONS entry (D12) when built.
