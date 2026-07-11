# ============================================================
# Environment: dev
# Target: Cisco DevNet always-on ACI sandbox
# ============================================================

terraform {
  required_providers {
    aci = {
      source  = "CiscoDevNet/aci"
      version = "~> 2.13"
    }
  }

  # Uncomment and configure to use remote state (recommended for teams)
  # backend "s3" {
  #   bucket = "your-tfstate-bucket"
  #   key    = "aci-gitops/dev/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aci" {
  username    = var.apic_username
  password    = var.apic_password
  url         = "https://${var.apic_host}"
  insecure    = true   # DevNet sandbox uses self-signed cert
}

module "tenant" {
  source = "../../modules/tenant"

  tenant_name        = "ACME-DEV-TF"
  tenant_description = "ACME Dev tenant — managed by Terraform"

  vrfs = [
    {
      name        = "DEV-VRF"
      description = "Dev VRF — unenforced"
      enforcement = "unenforced"
    }
  ]

  bridge_domains = [
    {
      name            = "DEV-BD"
      description     = "Dev bridge domain"
      vrf             = "DEV-VRF"
      gateway         = "192.168.10.1"
      mask            = "24"
      unicast_routing = true
      arp_flooding    = true
    }
  ]
}

output "tenant_dn" {
  value = module.tenant.tenant_dn
}
