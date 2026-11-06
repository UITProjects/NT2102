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
  description = "Static IP to set in Windows on ws-etw-0."
  value       = "${cidrhost(local.subnet_cidr, 10)}/${split("/", local.subnet_cidr)[1]}"
}

output "trainer_ip" {
  value = local.trainer_ip
}

output "suggested_static_ip_ws_etw_1" {
  description = "Static IP to set by hand in Windows on ws-etw-1."
  value       = "${local.windows_1_ip}/${split("/", local.subnet_cidr)[1]}"
}

output "vpn_ip" {
  description = "Static IP cloud-init gives nt2102-vpn (OpenVPN server)."
  value       = "${local.vpn_ip}/${split("/", local.subnet_cidr)[1]}"
}

output "kali_subnet_cidr" {
  description = "Kali's own SDN subnet (VNet it2102k)."
  value       = local.kali_subnet_cidr
}

output "kali_ip" {
  description = "Static IP to set in Kali by hand (no cloud-init on the ISO install)."
  value       = "${local.kali_ip}/${split("/", local.kali_subnet_cidr)[1]}"
}
