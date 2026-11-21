# src/audit-python/: auditd shipper (retired)

Linux counterpart of the Windows collector: tails `/var/log/audit/audit.log` and pushes every auditd record to
Loki. Retired with the Linux endpoint, which was replaced by a second Windows VM (DEVIATIONS D11). Raw logs only:
the pipeline has no converter or model for auditd records.

## Architecture

```
kernel audit rules (exec, net, file watches; own user excluded) ─► auditd ─► audit.log
  ─► auditship (systemd): tail from saved {inode, offset}, follow rotation
  ─► Loki /push   labels {job="auditd", host}, metadata {type, key}, ts = event time
```
