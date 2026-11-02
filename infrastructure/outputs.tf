output "vm_id" {
  description = "Proxmox VM ID."
  value       = proxmox_virtual_environment_vm.windows.vm_id
}

output "vnet" {
  description = "SDN VNet (bridge) the VM is attached to."
  value       = proxmox_sdn_vnet.lab.id
}

output "subnet_cidr" {
  value = local.subnet_cidr
}

output "gateway" {
  value = local.subnet_gateway
}

output "suggested_static_ip" {
  description = "Static IP to set in Windows (.10 like ws-etw in the Vagrant lab)."
  value       = "${cidrhost(local.subnet_cidr, 10)}/${split("/", local.subnet_cidr)[1]}"
}

output "trainer_ip" {
  value = local.trainer_ip
}

output "ubuntu_etw_ip" {
  description = "Static IP cloud-init gives ubuntu-etw."
  value       = "${local.ubuntu_etw_ip}/${split("/", local.subnet_cidr)[1]}"
}

output "vpn_ip" {
  description = "Static IP cloud-init gives nt2102-vpn (OpenVPN server)."
  value       = "${local.vpn_ip}/${split("/", local.subnet_cidr)[1]}"
}
