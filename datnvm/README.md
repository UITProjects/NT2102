# Lab NT2102.11.CH — Network Intrusion Detection

Vagrant-based lab for the 5 attack scenarios from the research phase:
DDoS, Brute-Force, Port Scan, Botnet (C2 beaconing), Web Attack (SQLi/XSS).

## Directory layout

```
lab-nt2102/
├── Vagrantfile
├── README.md
├── .gitignore
├── provision/                 # per-VM shell provisioners (in Git)
│   ├── _lib.sh                # shared helpers (routes, data-disk mount)
│   ├── attacker.sh
│   ├── web-victim.sh
│   ├── srv-victim.sh
│   ├── c2.sh
│   ├── bot.sh
│   └── sensor.sh
├── src/                       # YOUR shared code (in Git) → mounted at ~/NT2102 in every VM
│   └── README.md
└── .vm-data/                  # per-VM dedicated data disks (NOT in Git — see .gitignore)
    ├── attacker.vdi
    ├── web-victim.vdi
    ├── srv-victim.vdi
    ├── c2.vdi
    ├── bot.vdi
    └── sensor.vdi             # bigger (20 GB) — captures + flows live here
```

**What lives where:**
- `provision` — provisioning scripts, committed.
- `src` — the team's shared source code. Mounted at `/home/vagrant/NT2102` inside every VM. Two-way sync. **Commit here.**
- `.vm-data/` — per-VM data disks (VDI files). Attached to each VM at SATA port 2, formatted ext4, mounted at `/data` inside the guest. Survives `vagrant destroy`. **Not committed.**

## Subnet plan (encoded from `NT2102.11.CH`)

| Segment | CIDR | Notes |
|---|---|---|
| Internal LAN (VLAN 11) | `10.21.2.0/24` | "21.02" ≈ NT**2102**; `.11` = section `.11` |
| Attacker "Internet" | `198.51.100.0/24` | RFC 5737 TEST-NET-2 |
| C2 "Internet" | `203.0.113.0/24` | RFC 5737 TEST-NET-3 |
| Domain suffix | `nt2102.ch` | `.ch` is Switzerland's real ccTLD — no RFC conflicts |

All three run over **VirtualBox internal networks (`intnet`)**. That's important: VirtualBox 6.1.28+ restricts *host-only* adapters to `192.168.56.0/21` by default, but internal networks have **no such restriction**, so the three CIDRs above work out of the box without editing `/etc/vbox/networks.conf`.

## VMs

| Name | IP(s) | OS | Data disk | Role |
|---|---|---|---|---|
| `attacker` | `198.51.100.102` | Kali rolling | 5 GB | hping3 / nmap / hydra / patator / sqlmap / slowhttptest |
| `web-victim` | `10.21.2.12` | Ubuntu 22.04 | 5 GB | Apache + DVWA (SQLi / XSS / L7-DDoS target) |
| `srv-victim` | `10.21.2.13` | Ubuntu 22.04 | 5 GB | SSH + FTP + MySQL (brute-force / port scan target) |
| `c2` | `203.0.113.102` | Ubuntu 22.04 | 5 GB | Fake C2 HTTP endpoint (:8080) — logs to `/data/c2.log` |
| `bot` | `10.21.2.14` | Ubuntu 22.04 | 5 GB | Periodic beacon to C2 (~60s, low jitter) |
| `sensor` | `10.21.2.11`, `198.51.100.11`, `203.0.113.11` | Ubuntu 22.04 | **20 GB** | Zeek + Suricata + CICFlowMeter, also the router |

The `.11` on the sensor is deliberate — matches section `.11`. The sensor has three internal-network NICs (one per segment) with promiscuous mode on, so Zeek and Suricata see all LAN + attacker + C2 traffic.

## What you get inside every VM

- `/data` — the dedicated per-VM data disk, mounted read/write. Owned by `vagrant`.
- `/home/vagrant/NT2102` — the host's `src` folder, two-way synced (edits from host or guest are visible immediately on the other side).
- `/vagrant` — the host's project root (this whole directory), also synced. Handy for one-off scripts, but prefer `src` for shared code and `/data` for persistent artifacts.

## Prerequisites

- Vagrant ≥ 2.3
- VirtualBox ≥ 7.0 (default provider; also works with libvirt: `vagrant up --provider=libvirt`)
- ~16 GB free RAM, ~60 GB free disk (Kali box ~5 GB + Ubuntu box + VDI data disks)

First `vagrant up` takes 15–20 minutes (box downloads + `apt-get install` + DVWA clone).

Each VM is provisioned automatically once, when it is first created. Later `vagrant up` and `vagrant reload` operations do not rerun provisioning; use `vagrant provision <vm-name>` when you deliberately need to rerun it.

Provisioning starts the lab services and routes immediately, so a reboot is not ordinarily needed. If a provisioner detects an OS update that requires one, it prints a `vagrant reload <vm-name>` command at the end of its output.

## Bring it up

```bash
git clone <this-repo> lab-nt2102 && cd lab-nt2102
vagrant up                    # boot all 6 VMs
vagrant ssh attacker          # into a VM
```

Selective / cleanup:

```bash
vagrant up sensor srv-victim  # only what you need
vagrant halt                  # power off all
vagrant destroy -f            # nuke VMs — data disks stay in .vm-data/
rm -rf .vm-data && vagrant up # full reset (fresh data disks)
```

## Networking model

The `10.21.2.0/24` LAN is a VirtualBox host-only network, so the host can reach its VMs directly (for example, DVWA at `http://10.21.2.12/dvwa/`). The host conventionally uses `10.21.2.1`; the LAN guests keep their documented addresses. The attacker (`198.51.100.0/24`) and C2 (`203.0.113.0/24`) segments remain VirtualBox internal networks.

The sensor is the only VM sitting on all three lab networks; it has `net.ipv4.ip_forward=1`, so it routes between them. Every other VM has **specific static routes** to the two lab networks it isn't directly on, installed as a `lab-routes.service` systemd oneshot so they survive `vagrant halt`+`vagrant up`. The default route on every VM still goes through Vagrant's NAT adapter — that's what `apt-get update` uses. Lab traffic uses the specific routes.

After pulling this networking change, run `vagrant reload` for running VMs, or `vagrant up` for stopped VMs, so VirtualBox replaces the LAN adapter. Then open `http://10.21.2.12/dvwa/` from the host browser.

## Data-disk model

Every VM gets a second SATA disk (`.vm-data/<vm>.vdi`) attached at port 2, auto-formatted ext4 and mounted at `/data` on first boot (via a helper in `provision/_lib.sh` that finds "the empty unmounted disk that isn't the root disk", so it's portable across boxes that may or may not have a config-disk on port 1). On subsequent boots, `/etc/fstab` mounts it by UUID.

`vagrant destroy` calls a `before :destroy` trigger that detaches the disk from the VM and unregisters the medium from VirtualBox **without deleting the .vdi file**. Next `vagrant up` reattaches it and remounts it — so `/data` persists across destroy+up cycles. To wipe data, delete the .vdi manually.

## Quick demo

1. **Set DVWA security to Low** — from the attacker VM's browser, go to `http://10.21.2.12/dvwa/`, log in as `admin` / `password`, run Setup, switch security to Low.
2. **Start the sensor's capture** — on `sensor`:
   ```bash
   vagrant ssh sensor
   sudo ./capture.sh              # rotating tcpdump → /data/captures/
   ```
3. **Run attacks from `attacker`** — pre-generated scripts in `/home/vagrant/attacks/`:
   - `01_ddos_synflood.sh`, `02_ddos_slowloris.sh` — DDoS
   - `03_bruteforce_ssh.sh` — SSH brute-force
   - `04_portscan.sh` — nmap SYN scan
   - `05_web_sqli.sh`, `06_web_xss.sh` — web attacks (edit `PHPSESSID` first)
4. **Botnet beaconing** runs automatically — `bot` calls `c2:8080` every ~60s. Watch `/data/c2.log` on `c2`.
5. **Extract flow features** on `sensor`:
   ```bash
   ./run-cicflow.sh /data/captures/lab.pcap0   # offline PCAP → CSV in /data/flows/
   ./run-cicflow-live.sh                        # live capture → streaming CSV
   ./run-zeek.sh    /data/captures/lab.pcap0   # conn/http/dns logs to /data/zeek-logs/
   ```
6. **Train / score** using notebooks and scripts under `src` (accessible at `~/NT2102` from any VM, or directly on your host). CSVs land under `/data/flows/` on the sensor.

## Safety

The LAN (`10.21.2.0/24`) is host-only and therefore reachable from this host; it is not bridged to the real network. The attacker and C2 segments remain VirtualBox internal networks (`intnet`). The attacker VM cannot reach the outside world via its lab NIC; only via Vagrant's NAT (used for `apt-get`). **Do not run the attack scripts against anything other than the lab targets.**

## Troubleshooting

- **`storageattach` fails with "not found: SATA Controller"** — a small number of boxes rename the controller. Run `VBoxManage showvminfo nt2102-<name>` and check the storage controller name, then edit the `'--storagectl', 'SATA Controller'` line in the Vagrantfile.
- **Host-only IP-range error** — VirtualBox may reject the lab's `10.21.2.0/24` host-only range. On Linux, fix it with: `sudo mkdir -p /etc/vbox && echo '* 0.0.0.0/0 ::/0' | sudo tee /etc/vbox/networks.conf`.
- **"Medium with UUID … does not exist"** — happens if you manually deleted a `.vm-data/*.vdi` while VirtualBox still had it registered. Run `VBoxManage list hdds` to find the stale UUID and `VBoxManage closemedium disk <UUID>` to release it.
