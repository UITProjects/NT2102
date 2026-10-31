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
      type    = "in"
      action  = "ACCEPT"
      source  = local.subnet_cidr
      dest    = null
      log     = null
      comment = "it2102: Lab subnet (Kali/trainer -> RDP, DVWA)"
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
  input_policy  = "DROP"
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
