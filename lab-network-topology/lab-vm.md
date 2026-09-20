# NT2102.11.CH lab — component reference

Six VMs across three isolated virtual networks. The topology below — subnets, IPs, and what each host does — is provider-independent: it's just IP addressing and network segmentation, so it maps onto any hypervisor or virtualization tool (VirtualBox/Vagrant, VMware, libvirt/KVM, Proxmox, cloud VPCs/security groups, etc.). Whoever you share this with can rebuild it with whatever they already use — only the *how you provision it* changes, not the network design. Nothing here reaches the real internet except through NAT/DNS for package installs; the "internet" and "C2" segments are private RFC 5737 test-net ranges, not real networks.

## Networks

| Segment | Subnet | Purpose |
|---|---|---|
| `nt2102-lan` | 10.21.2.0/24 | The "corporate" internal network — victims and the bot live here. |
| `nt2102-internet` | 198.51.100.0/24 | Stand-in for "the internet" the attacker comes in from. |
| `nt2102-c2` | 203.0.113.0/24 | Stand-in network for command-and-control traffic. |

## VMs

### attacker — 198.51.100.102
Kali Linux, sitting alone on the internet segment. This is where you run the actual attacks against `web-victim` and `srv-victim` (web exploitation, brute force, etc.) — it has no route into the LAN except through whatever `sensor` forwards, so it plays the role of an external threat actor.

### web-victim — 10.21.2.12
Ubuntu box meant to run a vulnerable web app (DVWA per the naming/provision script) on the LAN. Target for web-layer attacks (SQLi, XSS, brute force login) that `attacker` launches across the network.

### srv-victim — 10.21.2.13
Ubuntu box meant to expose SSH/FTP/MySQL. Target for service-layer attacks — brute force, credential stuffing, exploitation of exposed daemons — distinct from the web-victim's HTTP-layer attack surface.

### c2 — 203.0.113.102
Ubuntu box acting as a fake command-and-control server, isolated on its own segment. `bot` (below) beacons out to it, generating the kind of periodic outbound traffic a real C2 channel produces, which is what your detection model is meant to pick up on.

### bot — 10.21.2.14
Ubuntu box on the LAN that's compromised by design — it periodically calls out to `c2` across the network. This simulates a bot/beacon inside the "corporate" network, giving you exfiltration/beaconing traffic to detect alongside the web and brute-force attacks.

### sensor — 10.21.2.11 / 198.51.100.11 / 203.0.113.11
The only multi-homed VM — it has a NIC on all three segments and doubles as:
- **Router**: forwards traffic between the LAN, the internet segment, and the C2 segment (all three private NICs are set to promiscuous mode so it can actually see traffic that isn't addressed to it).
- **Sensor**: runs Zeek, Suricata, and CICFlowMeter to capture and flow-export everything crossing it — this is where your labeled dataset for the anomaly-detection thesis comes from.