# `ws-etw-1` retrain, 2026-11-11: what was done and what it showed

Status lives in `docs/PROGRESS.md` (top section). This note is the walk-through for review: steps, numbers, what failed, and how to
repeat each step. All numbers come from one lab VM and operator-asserted labels: read them as counts, not rates (D2).

## 1. Why the range ends on 11-09

A scan of Loki `kproc` ProcessStart events for `ws-etw-1` showed the VM stopped being idle:

| When (Z) | What ran | Reading |
|---|---|---|
| 11-09 10:17 → 17:32 | RDP session: `git`, `msiexec`, `node`, `msedge`, `runas`, `curl` | software installed |
| 11-10 12:34, 15:15 | RDP: `winPEASany.exe`, `systeminfo`, `netsh`, `net`, `icacls` | privilege-escalation enumeration |
| 11-10 15:36 → now | Task Scheduler → `powershell` → `whoami`, every minute, session 0; reads `C:\Lab\Scripts\maintenance.ps1`, writes `C:\Lab\Output\result.txt` | a SYSTEM scheduled task |

None of it is in this repo and the user did not set it up. **Owner unconfirmed.** Training on that period would teach the model
`powershell → whoami` as normal, and `whoami` is one of the attack's discovery tools (the exp04 failure, D2).

So the baseline is the last clean stretch: `2026-11-06T20:30:00Z` → `2026-11-09T10:10:00Z`.

## 2. What was excluded

`src/artifacts/exclusions/ws-etw-1-20261111.json`, from a tally of all 3,456 process starts between 11-06 16:30Z and the end:

- 10 rotation canaries (`cmd /c whoami` + `python` at each new `live-*` run), 3 minutes each.
- RDP connected 11-07 03:40–03:56Z (`taskmgr`) and 11-08 16:41–20:56Z.
- Kept as OS work: Defender updates (`MsMpEng` → `wevtutil` ×26 on 11-07 14:30Z), servicing, the daily 04:19Z maintenance jobs.

Session 2 was logged on but disconnected for the whole range (its `taskhostw` runs every 8 h from 11-06 22:25Z, before the first RDP
connect). That is this VM's constant state, so only the connected periods are excluded.

Result: 1,848 windows built, 184 excluded, 1,664 kept (about 55 h); `check.json` ok; converter `cbbf6dc` = the live one.

## 3. Two things that went wrong on the way

- **First pull failed after 16 minutes.** A range starting 16:30Z includes run `live-20261106T142029Z`, which has no dos-map line in
  Loki. Its map is then guessed from file paths and lacks the CD-ROM entry, and the build refuses runs with different maps. Fix:
  start at the next run (20:30Z). Rule added to `training-runbook.md` §3.
- **First held-out replay scored 35 of 156 windows.** Starting the next replay session drops the windows still queued for scoring,
  and the trainer was busy (training + 4 replay servers). Fix: wait until the count of scored rows in `windows.csv` stops growing
  before starting the next run.

## 4. Gate: FILE passes, NET does not

`exp11-ws1`, flags as exp08 (`--layers 2 --val-windows 60 --tnr-tolerance 0.01`), 3 seeds, saved model = seed 360.

| Type | n_train | n_val | best_ep 360 / 361 / 362 | τ 360 / 361 / 362 |
|---|---|---|---|---|
| file | 127,694 | 4,078 | 99 / 99 / 99 | -0.006 / 0.113 / 0.015 |
| process | 14,230 | 550 | 99 / 31 / 12 | -0.090 / -0.058 / 1.957 |
| net | 4,189 | 204 | 14 / 9 / 4 | 0.764 / 0.885 / 2.824 |

For scale, exp10 on `ws-etw-0` reached `best_ep` 99 on every type and seed.

### Why NET stops early

The stopping rule keeps the latest epoch whose validation TNR is within `--tnr-tolerance` of the best TNR seen. TNR (true negative
rate) = the share of benign validation nodes that score below the threshold τ.

Two worked examples of what a tolerance of 0.01 means:

- FILE has 4,078 validation nodes. 0.01 × 4,078 ≈ 40 nodes may be above τ before an epoch is rejected.
- NET has 204 validation nodes. 0.01 × 204 ≈ 2 nodes. The third node above τ rejects the epoch.

A window is 5 minutes long and a new one starts every 2 minutes, so one event falls into up to 3 windows and becomes 3 node copies.
One unusual benign connection in the validation tail is therefore already 3 nodes: over the limit.

Seen in `train_scores.csv`:

- Held-out models (validation tail = 11-08 afternoon): on every seed the same 2 of 221 NET validation nodes are above τ. Both are
  `23.41.37.42:443`, the Defender signature download at 14:40Z, in windows `w1264` and `w1265`; its third copy (`w1263`) sits just
  below τ.
- Final model (validation tail = 11-09 morning): the 2 nodes are `10.21.3.255:138`, the NetBIOS broadcast address.

Why more data does not help: this host has 585 distinct NET peers in 62 h and 473 of them appear in 3 windows or fewer (one-off
update-server addresses). Any validation tail holds peers the training part never saw.

This differs from exp09-ws1, where the validation outlier was far above the training maximum (+3.47 vs −0.00). Here it is barely
above (held-out seed 361: validation max 0.460, training max 0.442).

## 5. Held-out idle windows

Three models (`exp11v-ws1-s360/361/362`) trained without 11-08 21:00Z → 11-09 10:10Z, then replayed on exactly those windows, next to
the old `exp09-ws1`. NET rule on, Moderate from 10. 156 windows scored by all four models: 152 idle, 4 canary.

| Model | Idle red | Moderate subgraph suppressed by the NET rule |
|---|---|---|
| exp09-ws1 | 2 (22:05Z sg 16.9; 04:15Z sg 67.7) | 0 |
| exp11v-ws1-s360 | 0 | 1 (01:25Z, 10.05) |
| exp11v-ws1-s361 | 0 | 1 (04:15Z, 24.47) |
| exp11v-ws1-s362 | 0 | 1 (04:15Z, 11.73) |

Held-out red: 0 / 0 / 0, mean ± std 0.0 ± 0.0. Limits:

- Only seed 362 trained NET and PROCESS to epoch 99. Seed 360's NET stopped at epoch 2 (τ 3.43): a NET model that flags almost
  nothing cannot satisfy the NET rule, so its green proves little (`retrain-2day-fp-lesson.md` §4).
- Without the NET rule each seed has 1 red window in 152.
- One 13 h stretch. Label source: operator assertion.

## 6. Preview of the VM as it is today

`exp11-ws1` on 11-10 16:00Z → 11-11 16:00Z, with the once-a-minute task running. Not benign evidence and not a detection result.

- 287 windows scored, 1 alert: 11-11 11:35Z, sg 10.20, a Defender update (`mpclient.dll`, `mpcommu.dll`, networking DLLs, new peer
  `23.56.25.40:443`).
- 1 Moderate subgraph suppressed by the NET rule (11-10 16:10Z, 10.24, file only).
- Median sg 0.00, p95 1.87. The `powershell → whoami` task raises nothing: its nodes score in-distribution, as lone short-lived
  processes did before (D6).

## 7. What this does and does not show

- Shown: 55 h of idle removes exp09-ws1's two idle false alarms on unseen windows, and fixes the FILE model.
- Not shown: a trustworthy NET model for `ws-etw-1`. The gate fails, so nothing was promoted and `ocr-live` is unchanged (one-VM
  mode, `ws-etw-0`, exp10).
- No attack has been run against `ws-etw-1`: no detection claim (D12).

## 8. Open decisions

1. NET on `ws-etw-1`: a stopping-rule setting for this host (for example a longer `--val-windows`, so 1 % is more than 2 nodes), as a
   separate named model compared on the same held-out windows; or leave `ws-etw-1` unscored.
2. Who owns `C:\Lab` on `ws-etw-1`, and does it stay.
3. Whether the 11-10 14:50Z kill-chain run on `ws-etw-0` was the user's (exp10 alerted: sg 69.09, LLM `attack` 0.9).

## 9. How to repeat (on the trainer, `cd ~/NT2102`)

```bash
X=src/artifacts/exclusions; EXP=range-20261106T203000Z-ws-etw-1
ARGS='--layers 2 --val-windows 60 --tnr-tolerance 0.01'

# pull + build (16 min for 62 h)
lab pull --start 2026-11-06T20:30:00Z --end 2026-11-09T10:10:00Z --split test --host ws-etw-1 --yes

# one held-out model per seed, then the final model
lab train --experiment $EXP --model exp11v-ws1-s360 --benign-split test --seeds 1 \
  --exclude-file $X/ws-etw-1-20261111.json --exclude-file $X/holdout-ws1-20261111.json --trainer-args "--seed 360 $ARGS"
lab train --experiment $EXP --model exp11-ws1 --benign-split test --seeds 3 \
  --exclude-file $X/ws-etw-1-20261111.json --trainer-args "$ARGS"

# replay one model on the held-out runs (one port per model), then compare
M=exp11v-ws1-s360; R=src/artifacts/runs/$M; OUT=src/out/etw/eval/ws1-holdout/$M
RUN_DIR=$R SCALER=$R/features/feature_scaler.json EXPERIMENT=$R/experiment.json PORT=8010 ./serve.sh --sessions-dir $OUT &
for run in live-20261108T210527Z live-20261109T031027Z live-20261109T091527Z; do
  PORT=8010 ./replay.sh src/out/etw/experiments/$EXP/raw/test/$run 120
  # wait here until the scored count in $OUT/replay-$run/windows.csv stops growing
done
lab compare --baseline src/out/etw/eval/ws1-holdout/exp09-ws1 --candidate src/out/etw/eval/ws1-holdout/exp11v-ws1-s360 \
  --candidate src/out/etw/eval/ws1-holdout/exp11v-ws1-s361 --candidate src/out/etw/eval/ws1-holdout/exp11v-ws1-s362
```

Long jobs ran under `systemd-run --slice=user.slice` (runbook §5). Models and replay output exist only on the trainer.

## 10. Resolution (2026-11-12): exp14-ws1 at `--tnr-tolerance 0.06`

> **Correction (2026-11-12, later the same day):** the cause given below ("2-3 brand-new benign peers") is wrong. The
> validation NET nodes over τ were mostly the NetBIOS broadcast `10.21.3.255:138`, which first appears on `ws-etw-1` at
> 11-09 09:19Z and so sat only in the validation tail; see §11. The numbers in this section stand.

The gate failure in §4 is the best-TNR early-stop keeping a loose, undertrained NET epoch: on this churny host NET validation TNR
peaks while the sphere is loose and **degrades monotonically** as it tightens (the ~200-node NET val set always holds 2-3 brand-new
benign peers a trained sphere correctly flags). So selection never picks a converged epoch.

- `--patience` is **not** the lever: exp13-ws1 (`--patience 15`) was byte-identical to exp12-ws1 (`--patience 5`) — NET best_ep still
  20/99/7 — because no later epoch ever re-qualifies as a tie, so a longer patience just runs further before restoring the same early
  "best".
- The lever is a tolerance **larger than the irreducible fresh-peer FPR** (a fully-trained NET here has val_fpr ≈ 0.0245). Sweep:
  0.03 → PROCESS fixed (99/99/99) but NET only 20/99/7; **0.06 → all types 99 on all seeds, NET τ −0.028/−0.073/−0.128 (stable).**
  FILE/PROCESS are unaffected (already converge at ep 99). `ws-etw-1` thus needs tol 0.06; `ws-etw-0` keeps 0.01.

```bash
EXP=range-20261106T203000Z-ws-etw-1; X=src/artifacts/exclusions
ARGS='--layers 2 --val-windows 60 --tnr-tolerance 0.06'
# final model (saved = seed 360)
lab train --experiment $EXP --model exp14-ws1 --benign-split test --seeds 3 \
  --exclude-file $X/ws-etw-1-20261111.json --trainer-args "$ARGS"
# held-out models (NET ep 99 on all 3 now), replay the 3 held-out runs with a drain-wait between, lab compare:
for S in 360 361 362; do
  lab train --experiment $EXP --model exp14v-ws1-s$S --benign-split test --seeds 1 \
    --exclude-file $X/ws-etw-1-20261111.json --exclude-file $X/holdout-ws1-20261111.json \
    --trainer-args "--seed $S $ARGS"; done
# serve each (RUN_DIR/SCALER/EXPERIMENT env, PORT 8010, --sessions-dir), replay, poll /api/status .session.scoring_queue==0 between runs.
lab compare --baseline src/out/etw/eval/ws1-holdout/exp09-ws1 \
  --candidate src/out/etw/eval/ws1-holdout-v14/exp14v-ws1-s360 (+s361 +s362)
```

Results: gate all best_ep 99; held-out idle red **0/0/0** (vs exp09-ws1 2); preview (`notidle-...`) 287 windows, **0 alerts** (median
0.00, p95 1.92, max 10.24). **NOT promoted** — Phase 3 (two-VM live) stays user-gated. One held-out idle window (04:15Z, seed 361) had
raw sg 117.0 suppressed by the NET rule, so 0-idle-red is partly load-bearing on that rule (D8, `subgraph-score-fragility.md`).

## 11. exp15-ws1 (2026-11-12): train on the VM's current state, deployed two-VM live

**Root cause, checked in data.** In exp14's saved model, 5 of the 6 validation NET nodes over τ are one address,
`10.21.3.255:138` (NetBIOS datagram broadcast, kernel `System`). On `ws-etw-1` it first appears 11-09 09:19Z and is in 17 of the
1,848 windows, all in the validation tail; after 11-10 it is in 112 of 287 windows. `ws-etw-0` has it in 231 training windows, which
is why its gate passes at tol 0.01. The "new peers" idea does not hold: `ws-etw-0` meets as many unseen peers (18 % of its validation
NET nodes) and flags none; the features describe behaviour (send/recv counts, idle times), not addresses.

**Fix:** put the VM's current behaviour into training. By user decision the teammate's setup and RDP sessions are included; only our
rotation canaries and the teammate's exploit / enumeration runs are excluded (bursts from Loki ProcessStart: `node.exe` descendants =
react2shell RCE, and winPEAS / `systeminfo` / `netsh` / `runas`+`whoami` / `icacls` in user sessions, first start − 5 min → last + 5 min).
The 11-09 10:19–10:33Z burst also used `net user`, `icacls`, `runas`, but for setup (account, ACLs, git clone, Node/npm install), so it
stays. VM clock = UTC+7 (the `hacked` file, 00:27 local, = 17:27Z inside an excluded burst).

```bash
# pull (lab pull has no --id; the default name was taken by exp11's experiment)
.venv/bin/python src/pipeline/loki_pull.py --start 2026-11-06T20:30:00Z --end 2026-11-12T12:15:00Z --split test --host ws-etw-1 \
  --yes --id range-20261106T203000Z-20261112T1215Z-ws-etw-1 --build --out-root src/out/etw/experiments
# seed 360 first (the deployed model is always run 0; training is deterministic), then 361/362 for the spread
lab train --experiment range-20261106T203000Z-20261112T1215Z-ws-etw-1 --model exp15-ws1 --benign-split test --seeds 1 \
  --exclude-file src/artifacts/exclusions/ws-etw-1-20261112.json --trainer-args "--layers 2 --val-windows 60 --tnr-tolerance 0.01 --verbose"
lab train ... --model exp15s-ws1 --seeds 2 --trainer-args "--seed 361 --layers 2 --val-windows 60 --tnr-tolerance 0.01 --verbose"
# deploy two-VM (from the workstation, then on the trainer)
ansible-playbook -i proxmox.ini ubuntu.yml --tags project --limit ubuntu-trainer --check --diff   # then without --check
lab promote exp10 --host ws-etw-0 && lab promote exp15-ws1 --host ws-etw-1 && lab hosts ws-etw-0 ws-etw-1
sudo systemctl restart ocr-live
```

Results: 4,070 windows, 6.04 M edges (about 10× exp14's training edges: post-setup windows are busy), 214 excluded. Gate at tol
0.01, seed 360: best_ep 99 on FILE / NET / PROCESS, val_fpr 0.0004 / 0.0000 / 0.0020. ~9.5 s per epoch, ~50 min per seed, 4.5 GB peak.
First live check: both engines on Loki, `ws-etw-1` 12 windows 0 red (max sg 1.99), `ws-etw-0` 12 windows 0 red. Status and open
items: `docs/PROGRESS.md` top section.
