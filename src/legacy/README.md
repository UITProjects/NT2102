# legacy/: replaced, kept for reference

Nothing here runs in the current lab. These files are kept for reference: they show how things were done before
Ansible and Loki, and the docs/notes and PROGRESS history still mention them.

| file | was | replaced by |
|---|---|---|
| `vagrant-provisioners/01-base.ps1` | Win10 VM base setup (Vagrant provisioner) | `src/ansible/roles/win_base` |
| `vagrant-provisioners/02-tools.ps1` | SilkETW + Invoke-AtomicRedTeam install | `roles/win_collector` (SilkETW). Atomic Red Team: not yet ported |
| `vagrant-provisioners/03-workload.ps1` | installs benign servers (Apache, PostgreSQL) | not ported |
| `vagrant-provisioners/04-collector.ps1` | SilkService config, `etwctl`, converter as a here-string | `roles/win_collector` + `src/pipeline/etw2nodlink.py` |
| `vagrant-provisioners/05-collect-for-20-minutes.ps1` | one capture + `etwwindow.py` as a here-string | `src/scripts/07-build-train-dataset.ps1` + `src/pipeline/etwwindow.py` |
| `vagrant-provisioners/08-live-shipper.ps1` | live capture + HTTP shipper (embedded `etwship.py`) | Alloy → Loki (`roles/win_alloy`, `src/live/loki_source.py`) |
| `sync_ps1.py` | kept 08's embedded copy of `etwship.py` current | not needed: Ansible copies `src/live/etwship.py` |

Still in use, so NOT here:
- `src/scripts/06-benign-workload.ps1` and `07-build-train-dataset.ps1`: Ansible deploys both.
- `src/live/etwship.py`: used for `--replay` and `--print-dos-map`, which `etwctl start` calls.
