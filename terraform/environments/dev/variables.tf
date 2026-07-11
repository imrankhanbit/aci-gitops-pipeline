variable "apic_host" {
  type        = string
  description = "APIC hostname or IP"
  default     = "sandboxapicdc.cisco.com"
}

variable "apic_username" {
  type        = string
  description = "APIC username"
  sensitive   = true
}

variable "apic_password" {
  type        = string
  description = "APIC password"
  sensitive   = true
}
