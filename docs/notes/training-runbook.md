# Runbook: train a model for one VM

Each VM has its own model, trained on its own idle data (DEVIATIONS D2, D12). Run on the trainer:

```bash
ssh -i ~/OneDrive/credentials/ssh/it2102-vms/id_rsa it2102@10.21.3.20
cd ~/NT2102 && source .venv/bin/activate
```

`ocr-live` can keep running. Everything over SSH runs in `user.slice`, capped at 50 % of RAM with 7 of 8 cores, so training cannot
starve it or the monitoring (`src/ansible/README.md`). A 3-day range peaked at 2,524 MiB (1 seed, 3 epochs); 10 h of data with 3 seeds
took 4 minutes.

## 1. Pick the range

- At least 24 h, so daily OS jobs are in the baseline: 3 days is the target (`retrain-2day-fp-lesson.md`).
- Loki keeps 72 h. Pull before the start of the range expires.
- Keep nothing from provisioning or deploys on that VM. Ansible, `choco` and installs flood kfile and are not idle.

## 2. Scan for bursts

Per-5-min counts by provider. Steady idle on a Windows VM is about 4k kfile and 400 kproc per 5 min.

```bash
HOST=ws-etw-1; START=$(date -d "2026-11-06T17:00:00Z" +%s); END=$(date +%s)
curl -s -G 127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode "query=sum by (provider) (count_over_time({job=\"silketw\",host=\"$HOST\"}[5m]))" \
  --data-urlencode "start=$START" --data-urlencode "end=$END" --data-urlencode "step=300" | python3 -m json.tool | less
```

A gap is harmless: no window is built across one. A spike needs a reason.

## 3. Check who ran

List every process start in the range: Loki `{job="silketw",host="<vm>",provider="kproc"} |= "ProcessStart"`, then tally the image name
in `XmlEventData.ImageName`.

- **Keep** recurring OS work, even when it is a big spike: `MpCmdRun` (Defender), `TiWorker`, `mousocoreworker`, `microsoftedgeupdate`.
  Missing it from the baseline causes false alarms.
- **Exclude** human or Ansible work: `wsmprovhost`, `rdpclip`, `mstsc`, `msiexec`, `choco`.
- **Exclude** the 6-h rotation canary: `cmd.exe /c whoami` plus `python` at each new `live-*` run. The attack runs `whoami` too.

## 4. Write the exclusion file

`src/artifacts/exclusions/<vm>-<date>.json`, one per VM. A window overlapping a range is dropped whole.

```json
{"ranges": [{"start": "2026-11-06T20:20:00Z", "end": "2026-11-06T20:35:00Z", "reason": "6-h rotation canary"}]}
```

## 5. Pull, build, train

Run it as a service so it survives losing SSH. It stays inside the `user.slice` cap.

```bash
sudo systemd-run --unit=nt2102-train --slice=user.slice --uid=$(id -u) --gid=$(id -g) \
  --working-directory=$HOME/NT2102 --setenv=HOME=$HOME --setenv=PYTHONUNBUFFERED=1 \
  .venv/bin/lab loop --start <t> --end <t> --split test --benign-split test --host <vm> --model <name> --seeds 3 \
  --exclude-file src/artifacts/exclusions/<file>.json \
  --trainer-args '--layers 2 --val-windows 60 --tnr-tolerance 0.01' --yes
journalctl -u nt2102-train -f
```

- A trained model is never overwritten: use a new `--model`.
- The experiment folder is `range-<UTC start>-<vm>`. If it exists, the pull stops: change the start, or add `--force` to replace it.
- The saved model is seed 360. The other seeds exist to show spread. Never pick a seed by its attack score.

## 6. Gate

Do not promote on "it trained". A nearly untrained model passes simple checks and looks like "no false positives".

| Check | Pass | exp08, for scale |
|---|---|---|
| `best_ep` | near the epoch limit on every type | 99 / 99 / 99 |
| τ across seeds | same scale, no type far from the others | file -0.07, net -0.13, process -0.16 |
| `val_fpr` | near 0 | 0 |
| `emb_std` | not 0 | 0.09 to 0.12 |
| `train_flag_rate` | about 0.001 | |
| `converter_check` | `match` | |

If `best_ep` is low, compare validation and training maxima in `train_scores.csv`. A validation node far above the training maximum
means the tail holds behaviour the training part never saw: that is a data problem, so get more data. Do not tune around it
(`--patience 20` changed nothing on exp09-ws1).

## 7. Promote

```bash
lab promote <name> --host <vm>
lab hosts                          # who is assigned, who is scored
sudo systemctl restart ocr-live    # needed if this VM is scored
```

Back up the run to the workstation if it should survive a rebuild of the trainer. `src/live/README.md` covers `lab hosts`.
