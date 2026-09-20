# NT2102.11.CH Network Intrusion Detection Lab

This repository is a master's-course prototype for **NT2102.11.CH** at UIT. It
contains a Vagrant/VirtualBox lab for generating, capturing, and analysing
synthetic intrusion traffic. Treat the checked-in `Vagrantfile` and
`provision/` scripts (including intentional uncommitted changes) as the source
of truth when they disagree with `README.md` or historical conversation
context.

## Project objective

Stage 1 is a working anomaly-detection demo for five traffic behaviours:

1. DDoS: abrupt abnormal traffic-volume increase.
2. Brute force: concentrated failed connection/login attempts (SSH/FTP).
3. Port scanning: many destinations or ports in a short window.
4. Botnet/C2 beaconing: periodic, low-jitter client callbacks.
5. Web attacks: SQL injection and XSS request patterns.

The intended modelling direction is **semi-supervised anomaly detection**:
train on Normal traffic only, tune a decision threshold using held-out Normal
and a mixed validation set, then test against Normal plus attacks. Do not
silently turn this into a multiclass supervised task; that is Stage 2+ work
unless the user explicitly asks for it.

The CICIDS2017 research dataset is the planned primary benchmark, with labels
collapsed to Normal, DoS/DDoS, PortScan, Brute Force, Web Attack, Botnet ARES,
and Infiltration. No full CICIDS2017 dataset is currently stored in this repo.

## Evaluation expectations

- Use a time-based split (for example, early days for train and later days for
  test) rather than a random flow split; source-IP split is a stricter
  alternative.
- Fit training data using Normal samples only. Log-transform volume-like
  fields (bytes, packets, duration) before `StandardScaler`.
- Report PR-AUC and ROC-AUC, but prioritise PR-AUC because of imbalance.
  Never use plain accuracy as the headline metric.
- At the selected threshold, report Normal false-positive rate, per-attack
  detection rate, threshold-selection method/value, and inference latency per
  flow. Ask for the split and these details when interpreting incomplete
  results.
- Treat results on rare classes cautiously: the planned Infiltration class has
  only about 29 examples, so its recall has high uncertainty.
- Baselines, in order: Isolation Forest, dense autoencoder, VAE, Deep SVDD;
  sequence/LSTM autoencoder only if time permits. Use Python with pandas,
  NumPy, scikit-learn, and preferably PyTorch for deep models.
- Flow features alone may miss web payload attacks and periodic beacons.
  RITA-style regularity analysis on Zeek logs is the planned beaconing
  complement; HTTP-payload modelling is a later/stretch complement.

## Current lab topology

The lab has six VMs. Every VM also has Vagrant's NAT adapter for package
installation; this is not lab traffic.

| VM | Address(es) | Current role |
| --- | --- | --- |
| `attacker` | `198.51.100.102` | Kali attack generator |
| `web-victim` | `10.21.2.12` | Ubuntu 22.04, Apache + DVWA |
| `srv-victim` | `10.21.2.13` | Ubuntu 22.04, SSH + vsftpd + MariaDB |
| `c2` | `203.0.113.102` | Ubuntu 22.04, fake HTTP C2 on port 8080 |
| `bot` | `10.21.2.14` | Ubuntu 22.04, periodic C2 beacon client |
| `sensor` | `10.21.2.11`, `198.51.100.11`, `203.0.113.11` | Ubuntu 22.04, router and sensor |

Network segments:

| Segment | CIDR | VirtualBox mode |
| --- | --- | --- |
| LAN (VLAN 11) | `10.21.2.0/24` | host-only (`private_network` default) |
| Attacker network | `198.51.100.0/24` | internal network `nt2102-internet` |
| C2 network | `203.0.113.0/24` | internal network `nt2102-c2` |

`sensor` is the sole router between lab networks and enables IPv4 forwarding.
Other guests install persistent specific routes through it. The LAN is
reachable from the host (normally host address `10.21.2.1`); it is not bridged
to a real network. Use exact IPs/hostnames above rather than inventing new
ones.

## Provisioning and artifacts

- `Vagrantfile` is the current topology/configuration authority. It uses the
  `vagrant-disksize` plugin and caps each VM's default disk at 20 GB.
- `attacker`, `web-victim`, `srv-victim`, `c2`, and `bot` provision on first
  creation. `sensor` has `run: "never"`: provision it intentionally with
  `vagrant provision sensor --provision-with sensor-setup` after it is up.
- The sensor installs Zeek, Suricata, tcpdump/tshark, and the Python
  `cicflowmeter` CLI. It runs as the router and uses promiscuous mode on the
  three lab NICs.
- Current sensor output location is host directory `sensor-data/`, mounted in
  the guest as `/vagrant/sensor-data`. It contains `captures/`, `flows/`, and
  `zeek-logs/`; guest convenience symlinks live under `/home/vagrant/`.
  This is intentionally different from the stale README references to sensor
  `/data` and `.vm-data/sensor.vdi`.
- The non-sensor provisioning scripts still call `mount_data_disk /data`, but
  the current `Vagrantfile` does not attach second data disks. Do not assume
  `/data` is functional on those VMs without reconciling that implementation
  mismatch first.
- `src/` is mounted read/write into every VM at `/home/vagrant/NT2102` and is
  the shared location for project code. Keep large PCAPs, flow CSVs, and model
  artifacts out of Git unless the user explicitly changes that policy.
- `pyproject.toml` currently targets Python `>=3.12` with pandas, Jupyter,
  openpyxl, and fastparquet. Add ML dependencies deliberately when model code
  is introduced.

Useful generated guest helpers after sensor provisioning:

```bash
sudo ~/capture.sh
~/run-zeek.sh /vagrant/sensor-data/captures/lab.pcap0
~/run-cicflow.sh /vagrant/sensor-data/captures/lab.pcap0
~/run-cicflow-live.sh
```

## Safe collaboration rules

- This is a synthetic academic lab. Help with the lab's own traffic generation,
  capture, detection, and VM provisioning, but do not adapt its attack tooling
  for external or real-world targets.
- The attacker scripts target only `10.21.2.12` or `10.21.2.0/24` in the lab.
  Preserve this scoping in changes and examples.
- Read a concrete log/error before recommending a fix. Likely lab-specific
  causes include host-only address restrictions, stale VirtualBox media UUIDs,
  storage-controller naming, guest routes, and Zeek/Suricata interface binding.
- Preserve existing user changes. The current working tree intentionally has
  modifications to `Vagrantfile` and `provision/sensor.sh`; do not overwrite or
  revert them unless explicitly asked.
- Default to English for code and formal documentation. Mirror Vietnamese for
  conversational discussion when the user does.

## Documentation maintenance

When altering topology, provisioning, storage, capture paths, or model
workflow, update the relevant parts of `README.md` and this file in the same
change. In particular, reconcile the remaining README data-disk description
before presenting it as current setup documentation.
