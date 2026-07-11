output "tenant_dn" {
  description = "Distinguished name of the created ACI tenant"
  value       = aci_tenant.this.id
}

output "tenant_name" {
  description = "Name of the created ACI tenant"
  value       = aci_tenant.this.name
}

output "vrf_dns" {
  description = "Map of VRF names to their distinguished names"
  value       = { for k, v in aci_vrf.this : k => v.id }
}

output "bd_dns" {
  description = "Map of BD names to their distinguished names"
  value       = { for k, v in aci_bridge_domain.this : k => v.id }
}
