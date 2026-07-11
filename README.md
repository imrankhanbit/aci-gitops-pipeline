# ACI GitOps Pipeline

A production-grade GitOps pipeline for Cisco ACI tenant provisioning. Tenant configurations are declared as YAML files, validated and deployed through a GitHub Actions CI/CD pipeline, with automated ServiceNow change management and post-deploy health checks.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Developer Workflow                                             │
│                                                                 │
│  1. Edit YAML  →  2. Open PR  →  3. CI validates  →  4. Merge  │
└─────────────────────────────────────────────────────────────────┘
          │                  │                         │
          ▼                  ▼                         ▼
   tenants/*.yml      GitHub Actions            GitHub Actions
   (source of truth)   validate.yml              deploy.yml
                       ┌──────────┐            ┌─────────────┐
                       │ lint     │            │ ServiceNow  │
                       │ tf plan  │            │ CR created  │
                       │ pytest   │            │     ↓       │
                       │ dry-run  │            │ Ansible     │
                       │ (DevNet) │            │ deploys to  │
                       └──────────┘            │ APIC        │
                                               │     ↓       │
                                               │ Health check│
                                               │     ↓       │
                                               │ CR closed   │
                                               └─────────────┘
```

## What this demonstrates

| Capability | Implementation |
|---|---|
| GitOps / IaC | YAML tenant configs as single source of truth |
| Cisco ACI automation | Ansible `cisco.aci` collection (VRF, BD, EPG, contracts) |
| Infrastructure as Code | Terraform `CiscoDevNet/aci` provider |
| CI/CD pipeline | GitHub Actions — lint, validate, dry-run, deploy |
| APIC REST API | Python scripts for health checks and fabric inventory |
| ServiceNow integration | Auto CR creation and evidence capture via REST API |
| Testing | `ansible-lint`, `terraform validate`, `pytest` with mocked APIC responses |

---

## Repository structure

```
aci-gitops-pipeline/
├── .github/
│   └── workflows/
│       ├── validate.yml        # PR: lint + plan + dry-run
│       └── deploy.yml          # Merge to main: deploy + health check
│
├── tenants/                    # Source of truth — edit these files
│   ├── prod-tenant.yml
│   └── dev-tenant.yml
│
├── ansible/
│   ├── playbooks/
│   │   ├── provision_tenant.yml
│   │   ├── validate_fabric.yml
│   │   └── rollback.yml
│   ├── roles/
│   │   ├── aci_tenant/         # Tenant / VRF / BD provisioning
│   │   ├── aci_networking/     # EPG / contract / L3Out
│   │   └── aci_security/       # Contract subjects / filters
│   ├── inventory/
│   │   └── hosts.yml
│   └── group_vars/
│       └── aci.yml
│
├── terraform/
│   ├── modules/
│   │   ├── tenant/             # ACI tenant + VRF + BD
│   │   ├── networking/         # EPGs + contracts
│   │   └── security/           # Contract filters + subjects
│   └── environments/
│       ├── dev/                # Dev APIC target
│       └── prod/               # Prod APIC target
│
├── python/
│   ├── apic_health.py          # Post-deploy fabric health check
│   ├── fabric_inventory.py     # Pull device + endpoint inventory
│   ├── servicenow_cr.py        # Auto CR creation and closure
│   └── tests/
│       ├── test_apic_health.py
│       └── test_servicenow_cr.py
│
├── docs/
│   ├── getting-started.md
│   └── pipeline-flow.md
│
├── Makefile                    # Local dev shortcuts
├── requirements.txt
└── ansible-requirements.yml
```

---

## Quick start

### Prerequisites

- Python 3.10+
- Ansible 8+
- Terraform 1.6+
- Access to [Cisco DevNet always-on ACI sandbox](https://devnetsandbox.cisco.com/DevNet/catalog/open-nxos-programmability_open-nx-os-programmability) or your own APIC
- ServiceNow developer instance at [developer.servicenow.com](https://developer.servicenow.com)

### 1. Clone and install dependencies

```bash
git clone https://github.com/YOUR_USERNAME/aci-gitops-pipeline.git
cd aci-gitops-pipeline

pip install -r requirements.txt
ansible-galaxy collection install -r ansible-requirements.yml
```

### 2. Configure credentials

Copy the example env file and fill in your sandbox credentials:

```bash
cp .env.example .env
```

```bash
# .env
APIC_HOST=sandboxapicdc.cisco.com
APIC_USERNAME=admin
APIC_PASSWORD=!v3G@!4@Y
SNOW_INSTANCE=https://your-instance.service-now.com
SNOW_USERNAME=admin
SNOW_PASSWORD=your-snow-password
```

> **Note:** Never commit `.env` to Git. It is in `.gitignore`. For GitHub Actions, add these as repository secrets.

### 3. Define your tenant

Edit or copy a tenant YAML file:

```yaml
# tenants/my-tenant.yml
tenant:
  name: "ACME-CORP"
  description: "ACME Corp production tenant"
  vrfs:
    - name: "PROD-VRF"
      enforcement: enforced
  bridge_domains:
    - name: "PROD-BD"
      vrf: "PROD-VRF"
      gateway: "10.10.10.1"
      mask: "24"
  epgs:
    - name: "WEB-EPG"
      bd: "PROD-BD"
      contracts:
        provided: ["WEB-TO-APP"]
        consumed: []
  contracts:
    - name: "WEB-TO-APP"
      subjects:
        - name: "HTTP-HTTPS"
          filters: ["HTTP", "HTTPS"]
```

### 4. Run the pipeline locally

```bash
# Validate and dry-run (safe — no changes to APIC)
make validate TENANT=tenants/my-tenant.yml

# Deploy to APIC
make deploy TENANT=tenants/my-tenant.yml

# Post-deploy health check
make health-check

# Roll back last deployment
make rollback TENANT=tenants/my-tenant.yml
```

### 5. Push to GitHub and let CI run

```bash
git checkout -b feature/add-acme-tenant
git add tenants/my-tenant.yml
git commit -m "feat: add ACME Corp production tenant"
git push origin feature/add-acme-tenant
# Open a Pull Request — GitHub Actions validate.yml runs automatically
```

---

## GitHub Actions secrets required

Add these in **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `APIC_HOST` | `sandboxapicdc.cisco.com` |
| `APIC_USERNAME` | `admin` |
| `APIC_PASSWORD` | Your APIC password |
| `SNOW_INSTANCE` | Your ServiceNow instance URL |
| `SNOW_USERNAME` | ServiceNow username |
| `SNOW_PASSWORD` | ServiceNow password |
| `SNOW_ASSIGNMENT_GROUP` | Your assignment group sys_id |

---

## Pipeline stages

### On Pull Request (`validate.yml`)

| Stage | Tool | What it checks |
|---|---|---|
| YAML lint | `yamllint` | Tenant YAML syntax |
| Ansible lint | `ansible-lint` | Playbook best practices |
| Terraform validate | `terraform validate` | HCL syntax |
| Terraform plan | `terraform plan` | What would change (no apply) |
| Unit tests | `pytest` | Python script logic (mocked API) |
| Dry-run | Ansible `check` mode | APIC changes preview (DevNet sandbox) |

### On Merge to main (`deploy.yml`)

| Stage | What happens |
|---|---|
| ServiceNow CR | Auto-created with change description and CI details |
| Terraform apply | ACI objects provisioned via TF state |
| Ansible deploy | Remaining objects pushed via cisco.aci collection |
| Health check | Python pulls APIC fabric health; fails pipeline if critical faults found |
| ServiceNow close | CR closed with deployment evidence (pipeline URL, timestamp, health output) |

---

## Sandbox targets

This repo is pre-configured for the **Cisco DevNet always-on ACI sandbox**:

- Host: `sandboxapicdc.cisco.com`
- Credentials: `admin / !v3G@!4@Y`
- No VPN required
- Shared environment — use unique tenant names to avoid conflicts

The sandbox resets periodically, so CI pipelines run against a clean state.

---

## Tech stack

`Cisco ACI` · `Ansible` · `cisco.aci collection` · `Terraform` · `CiscoDevNet/aci provider` · `Python` · `GitHub Actions` · `ServiceNow REST API` · `APIC REST API` · `YAML` · `Jinja2` · `pytest` · `ansible-lint`

---

## Author

Built by a Senior Network Automation Engineer with 14 years of DC infrastructure experience, 4 of them building production GitOps pipelines for Cisco ACI environments at scale.
