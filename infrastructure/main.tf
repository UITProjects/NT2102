locals {
  node_name        = "pve-master"
  vm_datastore_id  = "local-lvm"
  iso_datastore_id = "local"
  iso_url          = "https://software-static.download.prss.microsoft.com/sg/download/888969d5-f34g-4e03-ac9d-1f9786c66749/SERVER_EVAL_x64FRE_en-us.iso"
  iso_file_name    = "windows-server-2022.iso"
  sdn_zone_id      = "nt2102"
  vnet_id          = "it2102"
  subnet_cidr      = "10.21.3.0/24"
  subnet_gateway   = "10.21.3.1"
  vm_id            = 2102
  vm_name          = "ws-etw"
  disk_interface   = "sata0"
  nic_model        = "e1000e"
  lan_cidr         = "192.168.1.0/24"

  trainer_vm_id    = 2103
  trainer_name     = "ubuntu-trainer"
  trainer_ip       = "10.21.3.20"
  trainer_user     = "it2102"
  trainer_ssh_key  = trimspace(file("~/OneDrive/credentials/ssh/it2102-vms/id_rsa.pub"))
  ubuntu_image_url = "https://cloud-images.ubuntu.com/noble/20261017/noble-server-cloudimg-amd64.img"

  # Ubuntu endpoint (Linux counterpart of ws-etw): same cloud image + cloud-init as the trainer.
  ubuntu_etw_vm_id = 2104
  ubuntu_etw_name  = "ubuntu-etw"
  ubuntu_etw_ip    = "10.21.3.11"

  # OpenVPN server: same cloud image + cloud-init as ubuntu-etw.
  vpn_vm_id = 2105
  vpn_name  = "nt2102-vpn"
  vpn_ip    = "10.21.3.50"

  # Kali attacker: its OWN SDN VNet/subnet (10.21.4.0/24), separate from the lab subnet. Built from the
  # installer ISO (no Kali cloud image), so it is installed by hand in the console like ws-etw: no
  # cloud-init, IP/user/SSH set manually. Ansible then provisions it (src/ansible/kali.yml).
  kali_vnet_id        = "it2102k" # SDN VNet names: <= 8 alnum chars
  kali_subnet_cidr    = "10.21.4.0/24"
  kali_subnet_gateway = "10.21.4.1"
  kali_vm_id          = 2106
  kali_name           = "kali"
  kali_ip             = "10.21.4.30" # echoes the Vagrant Kali's .30 (src/Vagrantfile)
  # Version-pinned installer ISO. [INFERENCE] 2026.3 was the current release when written (verified to
  # resolve); confirm/bump against https://www.kali.org/get-kali before `apply` (like iso_url above).
  kali_iso_url       = "https://cdimage.kali.org/kali-2026.3/kali-linux-2026.3-installer-amd64.iso"
  kali_iso_file_name = "kali-linux-installer-amd64.iso"
  # Extra rule on top of it2102_rules (only this VM): VPN clients come from outside the lab subnet.
  vpn_rules = [
    {
      type    = "in"
      action  = "ACCEPT"
      source  = null
      dest    = null
      log     = null
      proto   = "tcp"
      dport   = "1194"
      comment = "it2102: OpenVPN clients (TCP 1194, DNAT from pve-master:2102)"
    },
  ]

  it2102_rules = [
    {
      type    = "out"
      action  = "DROP"
      source  = null
      dest    = local.lan_cidr
      log     = "info"
      comment = "it2102: VM -> home LAN"
    },
    {
      type    = "out"
      action  = "DROP"
      source  = null
      dest    = local.subnet_gateway
      log     = "info"
      comment = "it2102: VM -> PVE node via VNet gateway (UI/SSH); routing through it still works"
    },
    {
      type    = "out"
      action  = "DROP"
      source  = null
      dest    = local.kali_subnet_gateway
      log     = "info"
      comment = "it2102: VM -> PVE node via the Kali VNet gateway 10.21.4.1 (UI/SSH); the node has an IP on it2102k too"
    },
    {
      type    = "in"
      action  = "ACCEPT"
      source  = local.subnet_cidr
      dest    = null
      log     = null
      comment = "it2102: Lab subnet (Kali/trainer -> RDP, DVWA)"
    },
    {
      type    = "in"
      action  = "ACCEPT"
      source  = local.kali_subnet_cidr
      dest    = null
      log     = null
      comment = "it2102: Kali attacker subnet -> DVWA exploit + RCE callback (cross-subnet)"
    },
  ]
}

resource "proxmox_sdn_applier" "finalizer" {}

resource "proxmox_sdn_zone_simple" "lab" {
  id    = local.sdn_zone_id
  nodes = [local.node_name]
  ipam  = "pve"

  depends_on = [proxmox_sdn_applier.finalizer]
}

resource "proxmox_sdn_vnet" "lab" {
  id   = local.vnet_id
  zone = proxmox_sdn_zone_simple.lab.id

  depends_on = [proxmox_sdn_applier.finalizer]
}

resource "proxmox_sdn_subnet" "lab" {
  cidr    = local.subnet_cidr
  vnet    = proxmox_sdn_vnet.lab.id
  gateway = local.subnet_gateway
  snat    = true

  depends_on = [proxmox_sdn_applier.finalizer]
}

# Kali's own VNet + subnet in the same zone: a separate L2 segment with its own gateway + SNAT. Traffic
# to the lab subnet (10.21.3.0/24) is routed between the two gateways by the PVE node (not masqueraded),
# so ws-etw sees Kali's real 10.21.4.30 source.
resource "proxmox_sdn_vnet" "kali" {
  id   = local.kali_vnet_id
  zone = proxmox_sdn_zone_simple.lab.id

  depends_on = [proxmox_sdn_applier.finalizer]
}

resource "proxmox_sdn_subnet" "kali" {
  cidr    = local.kali_subnet_cidr
  vnet    = proxmox_sdn_vnet.kali.id
  gateway = local.kali_subnet_gateway
  snat    = true

  depends_on = [proxmox_sdn_applier.finalizer]
}

resource "proxmox_sdn_applier" "apply" {
  lifecycle {
    replace_triggered_by = [
      proxmox_sdn_zone_simple.lab,
      proxmox_sdn_vnet.lab,
      proxmox_sdn_subnet.lab,
    ]
  }

  depends_on = [
    proxmox_sdn_zone_simple.lab,
    proxmox_sdn_vnet.lab,
    proxmox_sdn_subnet.lab,
  ]
}

# A dedicated applier for the Kali VNet/subnet. proxmox_sdn_applier applies pending SDN config on
# create/replace (on_create defaults true), and replace_triggered_by only fires when a referenced
# resource is *replaced*, never on first creation. So adding kali to the main "apply" applier above would
# NOT reload the node the first time. A new applier here, created on this same apply, DOES run on_create
# and so applies the pending kali VNet/subnet to the node; replace_triggered_by then re-applies on any
# later change to them.
resource "proxmox_sdn_applier" "kali" {
  lifecycle {
    replace_triggered_by = [
      proxmox_sdn_vnet.kali,
      proxmox_sdn_subnet.kali,
    ]
  }

  depends_on = [
    proxmox_sdn_vnet.kali,
    proxmox_sdn_subnet.kali,
  ]
}

resource "proxmox_download_file" "windows_iso" {
  content_type   = "iso"
  datastore_id   = local.iso_datastore_id
  node_name      = local.node_name
  url            = local.iso_url
  file_name      = local.iso_file_name
  upload_timeout = 3600
}

resource "proxmox_virtual_environment_vm" "windows" {
  node_name   = local.node_name
  vm_id       = local.vm_id
  name        = local.vm_name
  description = "NT2102 Windows Server 2022 lab VM (manual install from ISO). Managed by Terraform."
  tags        = ["nt2102", "windows"]
  machine     = "q35"
  bios        = "ovmf"
  efi_disk {
    datastore_id      = local.vm_datastore_id
    type              = "4m"
    pre_enrolled_keys = true
  }

  tpm_state {
    datastore_id = local.vm_datastore_id
    version      = "v2.0"
  }

  operating_system {
    type = "win11"
  }
  cpu {
    cores = 4
    type  = "host"
  }
  memory {
    dedicated = 8192
  }
  disk {
    datastore_id = local.vm_datastore_id
    interface    = local.disk_interface
    size         = 256
    file_format  = "raw"
  }
  cdrom {
    file_id   = proxmox_download_file.windows_iso.id
    interface = "ide2"
  }

  boot_order = [local.disk_interface, "ide2"]

  network_device {
    bridge   = proxmox_sdn_vnet.lab.id
    model    = local.nic_model
    firewall = true
  }

  started         = true
  on_boot         = false
  stop_on_destroy = true

  depends_on = [proxmox_sdn_applier.apply]
}

resource "proxmox_virtual_environment_firewall_options" "windows" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.windows.vm_id

  enabled       = true
  input_policy  = "ACCEPT"
  output_policy = "ACCEPT"
}

resource "proxmox_virtual_environment_firewall_rules" "windows" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.windows.vm_id

  dynamic "rule" {
    for_each = local.it2102_rules
    content {
      type    = rule.value.type
      action  = rule.value.action
      source  = rule.value.source
      dest    = rule.value.dest
      log     = rule.value.log
      comment = rule.value.comment
    }
  }

  depends_on = [proxmox_virtual_environment_firewall_options.windows]
}

resource "proxmox_download_file" "ubuntu_image" {
  content_type = "import"
  datastore_id = local.iso_datastore_id
  node_name    = local.node_name
  url          = local.ubuntu_image_url
  file_name    = "it2102-noble-20261017.qcow2"
}

resource "proxmox_virtual_environment_vm" "trainer" {
  node_name   = local.node_name
  vm_id       = local.trainer_vm_id
  name        = local.trainer_name
  description = "NT2102 Ubuntu 24.04 trainer (Loki + Grafana, training, live server). Managed by Terraform."
  tags        = ["nt2102", "ubuntu"]

  operating_system {
    type = "l26"
  }
  cpu {
    cores = 4
    type  = "host"
  }
  memory {
    dedicated = 6144
  }

  scsi_hardware = "virtio-scsi-single"
  disk {
    datastore_id = local.vm_datastore_id
    import_from  = proxmox_download_file.ubuntu_image.id
    interface    = "scsi0"
    size         = 200
    iothread     = true
    discard      = "on"
  }

  network_device {
    bridge   = proxmox_sdn_vnet.lab.id
    model    = "virtio"
    firewall = true
  }

  serial_device {}

  initialization {
    datastore_id = local.vm_datastore_id
    ip_config {
      ipv4 {
        address = "${local.trainer_ip}/${split("/", local.subnet_cidr)[1]}"
        gateway = local.subnet_gateway
      }
    }
    dns {
      servers = ["1.1.1.1", "8.8.8.8"]
    }
    user_account {
      username = local.trainer_user
      keys     = [local.trainer_ssh_key]
    }
  }

  started         = true
  on_boot         = false
  stop_on_destroy = true

  depends_on = [proxmox_sdn_applier.apply]
}

resource "proxmox_virtual_environment_firewall_options" "trainer" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.trainer.vm_id

  enabled       = true
  input_policy  = "ACCEPT"
  output_policy = "ACCEPT"
}

resource "proxmox_virtual_environment_firewall_rules" "trainer" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.trainer.vm_id

  dynamic "rule" {
    for_each = local.it2102_rules
    content {
      type    = rule.value.type
      action  = rule.value.action
      source  = rule.value.source
      dest    = rule.value.dest
      log     = rule.value.log
      comment = rule.value.comment
    }
  }

  depends_on = [proxmox_virtual_environment_firewall_options.trainer]
}

resource "proxmox_virtual_environment_vm" "ubuntu_etw" {
  node_name   = local.node_name
  vm_id       = local.ubuntu_etw_vm_id
  name        = local.ubuntu_etw_name
  description = "NT2102 Ubuntu 24.04 endpoint (cloud image + cloud-init). Managed by Terraform."
  tags        = ["nt2102", "ubuntu"]

  operating_system {
    type = "l26"
  }
  cpu {
    cores = 2
    type  = "host"
  }
  memory {
    dedicated = 4096
  }

  scsi_hardware = "virtio-scsi-single"
  disk {
    datastore_id = local.vm_datastore_id
    import_from  = proxmox_download_file.ubuntu_image.id
    interface    = "scsi0"
    size         = 40
    iothread     = true
    discard      = "on"
  }

  network_device {
    bridge   = proxmox_sdn_vnet.lab.id
    model    = "virtio"
    firewall = true
  }

  serial_device {}

  initialization {
    datastore_id = local.vm_datastore_id
    ip_config {
      ipv4 {
        address = "${local.ubuntu_etw_ip}/${split("/", local.subnet_cidr)[1]}"
        gateway = local.subnet_gateway
      }
    }
    dns {
      servers = ["1.1.1.1", "8.8.8.8"]
    }
    user_account {
      username = local.trainer_user
      keys     = [local.trainer_ssh_key]
    }
  }

  started         = true
  on_boot         = false
  stop_on_destroy = true

  depends_on = [proxmox_sdn_applier.apply]
}

resource "proxmox_virtual_environment_firewall_options" "ubuntu_etw" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.ubuntu_etw.vm_id

  enabled       = true
  input_policy  = "ACCEPT"
  output_policy = "ACCEPT"
}

resource "proxmox_virtual_environment_firewall_rules" "ubuntu_etw" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.ubuntu_etw.vm_id

  dynamic "rule" {
    for_each = local.it2102_rules
    content {
      type    = rule.value.type
      action  = rule.value.action
      source  = rule.value.source
      dest    = rule.value.dest
      log     = rule.value.log
      comment = rule.value.comment
    }
  }

  depends_on = [proxmox_virtual_environment_firewall_options.ubuntu_etw]
}

resource "proxmox_virtual_environment_vm" "vpn" {
  node_name   = local.node_name
  vm_id       = local.vpn_vm_id
  name        = local.vpn_name
  description = "NT2102 Ubuntu 24.04 OpenVPN server (cloud image + cloud-init). Managed by Terraform."
  tags        = ["nt2102", "ubuntu", "vpn"]

  operating_system {
    type = "l26"
  }
  cpu {
    cores = 2
    type  = "host"
  }
  memory {
    dedicated = 1024
  }

  scsi_hardware = "virtio-scsi-single"
  disk {
    datastore_id = local.vm_datastore_id
    import_from  = proxmox_download_file.ubuntu_image.id
    interface    = "scsi0"
    size         = 10
    iothread     = true
    discard      = "on"
  }

  network_device {
    bridge   = proxmox_sdn_vnet.lab.id
    model    = "virtio"
    firewall = true
  }

  serial_device {}

  initialization {
    datastore_id = local.vm_datastore_id
    ip_config {
      ipv4 {
        address = "${local.vpn_ip}/${split("/", local.subnet_cidr)[1]}"
        gateway = local.subnet_gateway
      }
    }
    dns {
      servers = ["1.1.1.1", "8.8.8.8"]
    }
    user_account {
      username = local.trainer_user
      keys     = [local.trainer_ssh_key]
    }
  }

  started         = true
  on_boot         = false
  stop_on_destroy = true

  depends_on = [proxmox_sdn_applier.apply]
}

resource "proxmox_virtual_environment_firewall_options" "vpn" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.vpn.vm_id

  enabled       = true
  input_policy  = "ACCEPT"
  output_policy = "ACCEPT"
}

resource "proxmox_virtual_environment_firewall_rules" "vpn" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.vpn.vm_id

  # Shared it2102 rules first (first match wins), then the OpenVPN accept.
  dynamic "rule" {
    for_each = concat(local.it2102_rules, local.vpn_rules)
    content {
      type    = rule.value.type
      action  = rule.value.action
      source  = rule.value.source
      dest    = rule.value.dest
      proto   = try(rule.value.proto, null)
      dport   = try(rule.value.dport, null)
      log     = rule.value.log
      comment = rule.value.comment
    }
  }

  depends_on = [proxmox_virtual_environment_firewall_options.vpn]
}

# ---- Kali attacker VM (installer ISO, manual install; on its own VNet it2102k) --------------------
resource "proxmox_download_file" "kali_iso" {
  content_type   = "iso"
  datastore_id   = local.iso_datastore_id
  node_name      = local.node_name
  url            = local.kali_iso_url
  file_name      = local.kali_iso_file_name
  upload_timeout = 3600
}

resource "proxmox_virtual_environment_vm" "kali" {
  node_name   = local.node_name
  vm_id       = local.kali_vm_id
  name        = local.kali_name
  description = "NT2102 Kali attacker (manual install from ISO). Managed by Terraform; provisioned by src/ansible/kali.yml."
  tags        = ["nt2102", "kali", "attacker"]

  operating_system {
    type = "l26"
  }
  cpu {
    cores = 2
    type  = "host"
  }
  memory {
    dedicated = 4096
  }

  # Linux installer: SeaBIOS + virtio disk/NIC (Kali's installer has virtio drivers; no UEFI/TPM or a
  # driver ISO needed, unlike the Windows VM).
  scsi_hardware = "virtio-scsi-single"
  disk {
    datastore_id = local.vm_datastore_id
    interface    = "scsi0"
    size         = 40
    iothread     = true
    discard      = "on"
  }
  cdrom {
    file_id   = proxmox_download_file.kali_iso.id
    interface = "ide2"
  }

  # Disk first; an empty disk falls through to the CD for the install.
  boot_order = ["scsi0", "ide2"]

  network_device {
    bridge   = proxmox_sdn_vnet.kali.id
    model    = "virtio"
    firewall = true
  }

  serial_device {}

  # No initialization{} block: that is cloud-init, which the Kali installer ISO has none of. IP
  # (10.21.4.30/24, gw 10.21.4.1), the kali user's password and ssh are set by hand in the console.

  started         = true
  on_boot         = false
  stop_on_destroy = true

  # The kali applier (not the main one) is what applies the it2102k VNet to the node.
  depends_on = [proxmox_sdn_applier.kali]
}

resource "proxmox_virtual_environment_firewall_options" "kali" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.kali.vm_id

  enabled       = true
  input_policy  = "ACCEPT"
  output_policy = "ACCEPT"
}

resource "proxmox_virtual_environment_firewall_rules" "kali" {
  node_name = local.node_name
  vm_id     = proxmox_virtual_environment_vm.kali.vm_id

  # Shared it2102 rules: Kali accepts inbound from the lab subnet (the ws-etw -> Kali stage callback) and
  # may send outbound to ws-etw:3000 (the OUT DROPs only target the home LAN + the node gateway).
  dynamic "rule" {
    for_each = local.it2102_rules
    content {
      type    = rule.value.type
      action  = rule.value.action
      source  = rule.value.source
      dest    = rule.value.dest
      log     = rule.value.log
      comment = rule.value.comment
    }
  }

  depends_on = [proxmox_virtual_environment_firewall_options.kali]
}
