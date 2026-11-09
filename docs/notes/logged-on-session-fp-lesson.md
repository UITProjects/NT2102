# Lesson: a logged-on session is not idle (2026-11-09)

Rules for an idle baseline are in `retrain-2day-fp-lesson.md`; steps in `training-runbook.md`. This note adds one rule. Counts come from
one lab VM (`ws-etw-0`, model exp08) and operator-asserted labels: read them as counts, not rates (D2).

## What happened

exp08 was trained on idle data from 11-02 17:10Z to 11-05 15:49Z. On 2026-11-09 `ocr-live` raised **8 alerts from 10:30Z to 14:10Z**.
Every one the LLM judged (7) came back `false_positive` (0.72 to 0.93). Evidence: `src/live/sessions/history.jsonl` on the trainer;
kept subgraphs in `<session>/windows/<window>/subgraphs.json`; verdicts in `live-20261109T104414Z/llm_verdicts.csv`.

| Window (Z) | Session | sg (level) | Processes in the kept subgraph(s) |
|---|---|---|---|
| 10:30 | `live-20261109T082907Z` | 111.67 Significant | `tiworker` + `ngen` (servicing); `wmiprvse` (35.51) |
| 10:45 | `live-20261109T104414Z` | 34.29 (7 kept) | `svchost` ×15, `lsass`/`efsui`, `musnotification*`, `smartscreen`, edge update, `rdpclip` from 10.21.3.50 |
| 10:50 | same | 16.35 | Defender signature update (`mpam-d_*.exe`, `MpCmdRun`, `MsMpEng`) |
| 11:10, 12:10, 14:10 | same | 11.28 / 11.33 / 12.45 | `dsregcmd.exe` (device registration), every hour at :11 |
| 13:10 | same | 11.42 | :10 hourly slot again; subgraph not inspected (LLM `false_positive`) |
| 13:15 | same | 13.65 | `microsoftedgeupdate` + `wermgr` |

The process starts in Loki (`kproc` ProcessStart, ws-etw-0, 11-06 14:38Z → 11-09 14:39Z, 5,330 starts) show who started what:

- 10:30:02Z **RDP connect**: `rdpclip`, `logonui`, `atbroker` in session 1. 10:32:00Z **WinRM** (`wsmprovhost`). 10:32:05Z `dismhost`
  started by `WmiPrvSE`, then `TrustedInstaller` → `TiWorker` → `ngen`/`mscorsvw` ×50. Servicing that starts 5 s after a WinRM call
  looks remotely triggered, not like the monthly update.
- 10:46:22Z **interactive logon** in session 2 (`userinit` → `explorer`). **`dsregcmd` first runs at 10:47:22Z**, one minute later, then
  hourly at :11. It never ran in the 3 days before.
- Session 2's PowerShell ran `ping`/`ipconfig` at 11:02–11:08Z and again at 13:40Z. These are human commands, and the attack uses the same
  tools.

Two older patterns:
- **22:25Z every night** (11-06, 11-07, 11-08, sg ≈ 21.2): a lone `powershell.exe` started by the Task Scheduler `svchost`, the same
  parent as the 5-min `etwlab-live` watchdog (288 starts/day). [INFERENCE] One nightly watchdog run that the own-tooling filter misses.
  Open: not yet checked against `start-live.ps1`.
- **11-08 05:11Z**: `mysql` ×45, `schtasks`, discovery tools, `rclone`, C2 `10.21.4.30:8080` (sg 60.44). The kill chain, i.e. an attack run.
  It is not a false alarm.

## Why

The false alarms are **human activity on the VM, plus the state a logged-on session leaves behind**. They are not OS drift.
- An RDP or console logon starts per-user processes (`dsregcmd`, `explorer`, `smartscreen`, notification hosts). These keep running on a
  schedule while the session stays logged on, even after the window is closed.
- The exp08 baseline had RDP windows excluded and no session left logged on, so all of this is out of distribution. That is correct for
  an idle model (D2: "interactive sessions stay red").

My first reading ("a Windows Update shifted the OS") was wrong. The process tree shows the servicing burst followed a WinRM call, and
`dsregcmd` followed the logon.

## Rule

**Idle means no one is logged on.** After an RDP session on ws-etw-0, **sign out** (do not just close the window). A disconnected session
keeps scheduled per-user jobs running.

- Training: exclude every window from the logon until the sign-out, not only the minutes with visible commands. Retraining on that period
  would teach the model `ping`/`ipconfig`/`whoami`, which are the attack's own discovery tools (the exp04 failure mode).
- A retrain does not fix false alarms caused by human activity. Signing out does.
- Before the demo: sign out of every session on ws-etw-0, and run no Ansible against it in the last hour (11-06 15:00Z / 15:50Z were
  Ansible/git/choco false alarms too).

## Retention lesson

Loki kept 72 h. So "train on all the data we have" could only ever mean 3 days, and evidence windows (attacks, false alarms) expired
before anyone analysed them. Raised to 90 days on 2026-11-09 (`loki_retention`, D2). Data that had already expired (before about
11-06 14:30Z) is gone. Measured size: Loki chunks were 445 MB for about 3 days of both VMs, so 90 days is about 13 to 15 GB.

See DEVIATIONS D2, PROGRESS.
