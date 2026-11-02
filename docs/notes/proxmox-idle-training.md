# Runbook: train the Proxmox idle model (exp06)

Written 2026-11-02. Scope: training only (the attack / Kali VM on it2102 is a separate step).

## Why

`ocr-live` on the Proxmox trainer (10.21.3.20) still loads **exp01** (`src/artifacts/current.json`; the trainer
has no `current.local.json`, and its `src/artifacts/runs/` holds only exp01). exp01 and exp05 were trained on the
**Vagrant** lab: a different Windows install, 10.21.2.x addresses and other background activity. So Proxmox live
windows go red for a reason that has nothing to do with detection. The fix is to train **exp06** on a long,
clean idle range of the **Proxmox ws-etw** pulled from Loki (DEVIATIONS D2: benign = idle), then promote it.

## What Loki held at planning time (2026-11-02 ~12:25Z)

- Run `live-20261102T110409Z` (split `test`, host `ws-etw`).
- **11:05–11:40Z is a burst** (ws-etw boot + the ocr-live fault tests: kfile up to 585k and kproc up to 65k per
  5 min). It is **not idle**.
- **Steady idle since 11:40Z**: ~4k kfile, ~350–500 kproc and 0–36 knet per 5 min.
- Before that, nothing usable: no data at all from 2026-11-01 06:40Z to 2026-11-02 11:05Z.
- Live runs rotate every 6 h (the next one ~17:04Z). A range pull that crosses rotations gets several
  `raw/test/<run>/` folders. `build_experiment.py` accepts that as long as their dos maps agree (same VM).

**Dataset = idle from 2026-11-02 11:45Z up to the trigger.** A full 48 h ends ~2026-11-04 11:45Z (18:45 +07). At
midnight tonight (00:00 +07 = 17:00Z) there are only ~5 h. The commands are the same; only `--end` changes.

## Rule until the range ends

**Nobody touches ws-etw:** no `windows.yml` / `verify-windows.yml`, no `win_ping`, no WinRM shell, no RDP, no
reboot.

Why: a WinRM session spawns `wsmprovhost` + `powershell`. Inside the training range that would teach the
one-class model the attack's parent-process shape as normal (the exp04 failure mode). If it happens anyway,
write down the UTC time so step 2 can cut it out.

Trainer-side work is fine, including restarting `ocr-live`: it doesn't run on ws-etw.

## Prerequisite (done before midnight)

The `lab train --benign-split test` flag is committed on `provide-infrastructure`. Once it is pushed, run on the
trainer:

```bash
cd ~/NT2102 && git pull --ff-only && uv sync && source .venv/bin/activate && lab train -h | grep benign-split
```

Why the flag: an idle Loki pull is `--split test`, so the build writes `SimulatedW10_test_edges.csv`. Without
the flag `lab train` only reads `_train_` files. The flag is an explicit opt-in (a test split can contain an
attack), and it records `benign_split: test` + the pulled time range in `lineage.json`.

## Midnight steps (on the trainer)

```bash
ssh -i ~/OneDrive/credentials/ssh/it2102-vms/id_rsa it2102@10.21.3.20
cd ~/NT2102 && source .venv/bin/activate
```

### 1. Pick `--end`

Use the trigger time minus 10 min, so the shipper has flushed and the whole range is in Loki.

### 2. Scan the range for bursts (per-30-min counts by provider)

```bash
START=$(date -d "2026-11-02T11:45:00Z" +%s); END=$(date -d "<end>" +%s)
curl -s -G 127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query=sum by (provider) (count_over_time({job="silketw",host="ws-etw"}[30m]))' \
  --data-urlencode "start=$START" --data-urlencode "end=$END" --data-urlencode "step=1800" \
| python3 -c '
import sys, json, datetime as d
rows = {}
for r in json.load(sys.stdin)["data"]["result"]:
    for t, v in r["values"]:
        rows.setdefault(int(t), {})[r["metric"]["provider"]] = int(v)
for t in sorted(rows):
    x = rows[t]
    print(d.datetime.fromtimestamp(t, d.timezone.utc).strftime("%m-%d %H:%MZ"),
          *(f"{p} {x.get(p, 0):>8}" for p in ("kproc", "kfile", "knet")))'
```

Idle looks like ~24k kfile and ~2–3k kproc per 30 min.

- A **gap** (VM off, missing rows) is harmless: the windower never builds a window across a gap > 120 s.
- A **spike** is not harmless: Windows Update, a Defender scan, a reboot (a ~35-min boot burst like 11:05–11:40Z)
  or a WinRM/RDP session.
- With one or two spikes, take the longest clean stretch as `--start`/`--end`.
- If the clean data is split into several stretches that are each much shorter than the total, ask Claude for a
  multi-range pull. That doesn't exist yet: `loki_pull` takes one start/end.

### 3. Free memory

The trainer has 5.8 GB of RAM and `ocr-live` holds ~2.7 GB, so stop it during the build and the training:

```bash
sudo systemctl stop ocr-live
```

[INFERENCE] estimate for 48 h: ~2.5M kfile + ~250k kproc lines, about 2–3 GB of raw JSON and ~1,400 sliding
windows (5-min windows, 2-min stride). Disk is fine (180 GB free).

### 4. Pull + build

```bash
lab pull --start "2026-11-02T11:45:00+00:00" --end "<end>+00:00" --split test --yes
```

This writes `src/out/etw/experiments/range-20261102T114500Z/` (pass `--force` to redo it). Then check:

- `check.json`: `contract_ok` is true.
- `experiment.json`: `results.windows` is roughly what you expect for the range length.
- The build exits by itself if the runs' dos maps disagree.

### 5. Contamination check (EDA)

List the distinct process names in `SimulatedW10_test_node_attrs.csv` and the windows they occur in. Flag
`wsmprovhost`, `powershell`, `msiexec`, `choco`, `TiWorker`, `MpCmdRun`, `mstsc` and `rdpclip`.

- Recurring OS activity (Defender, scheduled tasks) is genuine idle and stays.
- One-off human or Ansible activity means: narrow the range and re-pull with `--force`.

### 6. Train

```bash
lab train --experiment range-20261102T114500Z --model exp06 --seeds 3 --benign-split test
```

### 7. Gate before promoting

- `src/artifacts/runs/exp06/models/` has a model for PROCESS, FILE and NET, and `emb_std ≠ 0` per type.
- From `train_meta.json`, record per type the node-copies and `val_fpr` (mean ± std over the 3 seeds; label
  source = operator-asserted idle). NET will be the thinnest (0–36 knet per 5 min). Report it, but don't block
  on it: SG-Det absorbed the per-node NET noise for exp05.
- Don't compare these numbers to exp05's as if they were equivalent: different host, different data.

### 8. Promote + restart

```bash
lab promote exp06            # converter_check must print: match
sudo systemctl start ocr-live
```

## Verification

- `curl -s 127.0.0.1:8000/healthz` returns 200 (the port comes from `server_port` in the Ansible vars).
- After the catch-up (6–16 min), **≥ 4 consecutive idle live windows are green**: alert False, subgraph max < 30
  (below Moderate). Check in the live UI or in the per-window JSON under `src/live/sessions/<id>/`.
- What this shows: "it runs", and "benign stays green on Proxmox data". It does **not** show detection, because
  a model that never alerts would pass too. The attack half (DVWA→RCE goes red) waits for the Kali VM on
  it2102.
- Back up to the workstation: `src/artifacts/runs/exp06/` plus the experiment's `experiment.json`, `check.json`
  and `pull.json` (`scp -i ~/OneDrive/credentials/ssh/it2102-vms/id_rsa -r it2102@10.21.3.20:~/NT2102/src/artifacts/runs/exp06 src/artifacts/runs/`).
  A Terraform rebuild of the trainer would lose both the model and the Loki data.
- Afterwards, update `docs/PROGRESS.md` (Proxmox model = exp06, the range, the gate numbers), CLAUDE.md §8
  (replace "ocr-live still loads exp01"), and the memory note `proxmox-lab`.
