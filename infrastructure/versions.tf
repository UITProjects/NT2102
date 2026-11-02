terraform {
  required_version = ">= 1.6"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.119.0" # exact pin; argument names checked against the v0.119.0 docs
    }
  }
}

provider "proxmox" {
  endpoint = var.proxmox_endpoint
  username = "root@pam"
  password = var.proxmox_root_password
  insecure = true
  ssh {
    agent       = true
    private_key = file("~/OneDrive/credentials/ssh/proxmox/id_ed25519")
  }
}
