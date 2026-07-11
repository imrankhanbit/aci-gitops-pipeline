variable "tenant_name" {
  type        = string
  description = "ACI tenant name"
}

variable "tenant_description" {
  type        = string
  description = "Human-readable description for the tenant"
  default     = ""
}

variable "vrfs" {
  type = list(object({
    name        = string
    description = optional(string, "")
    enforcement = optional(string, "enforced")
  }))
  description = "List of VRFs to create within the tenant"
}

variable "bridge_domains" {
  type = list(object({
    name               = string
    description        = optional(string, "")
    vrf                = string
    gateway            = optional(string)
    mask               = optional(string)
    unicast_routing    = optional(bool, true)
    arp_flooding       = optional(bool, false)
    l2_unknown_unicast = optional(string, "proxy")
  }))
  description = "List of Bridge Domains to create within the tenant"
}
