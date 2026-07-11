# ============================================================
# Terraform module: tenant
# Provisions an ACI tenant, VRFs, Bridge Domains, and subnets.
# Complements the Ansible roles — use either or both.
# ============================================================

terraform {
  required_providers {
    aci = {
      source  = "CiscoDevNet/aci"
      version = "~> 2.13"
    }
  }
}

# ── Tenant ──────────────────────────────────────────────────
resource "aci_tenant" "this" {
  name        = var.tenant_name
  description = var.tenant_description
  annotation  = "orchestrated-by:aci-gitops-pipeline"
}

# ── VRFs ────────────────────────────────────────────────────
resource "aci_vrf" "this" {
  for_each = { for vrf in var.vrfs : vrf.name => vrf }

  tenant_dn              = aci_tenant.this.id
  name                   = each.value.name
  description            = lookup(each.value, "description", "")
  pc_enf_dir             = "ingress"
  pc_enf_pref            = lookup(each.value, "enforcement", "enforced")
  bd_enforced_enable     = "no"
  knw_mcast_act          = "permit"
  annotation             = "orchestrated-by:aci-gitops-pipeline"
}

# ── Bridge Domains ──────────────────────────────────────────
resource "aci_bridge_domain" "this" {
  for_each = { for bd in var.bridge_domains : bd.name => bd }

  tenant_dn          = aci_tenant.this.id
  name               = each.value.name
  description        = lookup(each.value, "description", "")
  relation_fv_rs_ctx = aci_vrf.this[each.value.vrf].id
  unk_mac_ucast_act  = lookup(each.value, "l2_unknown_unicast", "proxy")
  arp_flood          = lookup(each.value, "arp_flooding", false) ? "yes" : "no"
  unicast_route      = lookup(each.value, "unicast_routing", true) ? "yes" : "no"
  annotation         = "orchestrated-by:aci-gitops-pipeline"
}

# ── Bridge Domain Subnets (gateways) ────────────────────────
resource "aci_subnet" "this" {
  for_each = {
    for bd in var.bridge_domains : bd.name => bd
    if lookup(bd, "gateway", null) != null
  }

  parent_dn  = aci_bridge_domain.this[each.key].id
  ip         = "${each.value.gateway}/${each.value.mask}"
  scope      = ["public"]
  annotation = "orchestrated-by:aci-gitops-pipeline"
}
