# ACI GitOps Pipeline

[![Validate — PR Check](https://github.com/imrankhanbit/aci-gitops-pipeline/actions/workflows/validate.yml/badge.svg)](https://github.com/imrankhanbit/aci-gitops-pipeline/actions/workflows/validate.yml)
[![Deploy — Merge to Main](https://github.com/imrankhanbit/aci-gitops-pipeline/actions/workflows/deploy.yml/badge.svg)](https://github.com/imrankhanbit/aci-gitops-pipeline/actions/workflows/deploy.yml)

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
                       ┌──────────┐            ┌─────────────────┐
                       │ YAML lint│            │ Open ServiceNow │
                       │ Ans lint │            │ CR (New→Sched.) │
                       │ TF plan  │            │       ↓         │
                       │ pytest   │            │ Ansible deploy  │
                       │ dry-run  │            │ to Cisco APIC   │
                       └──────────┘            │       ↓         │
                                               │ Health check    │
                                               │ (APIC REST API) │
                                               │       ↓         │
                                               │ Close CR        │
                                               │ (Impl→Closed)   │
                                               └─────────────────┘
```

## What this demonstrates

| Capability | Implementation |
|---|---|
| GitOps / IaC | YAML tenant configs as single source of truth |
| Cisco ACI automation | Ansible `cisco.aci` collection (VRF, BD, EPG, contracts) |
| Infrastructure as Code | Terraform `CiscoDevNet/aci` provider |
| CI/CD pipeline | GitHub Actions — lint, validate, dry-run, deploy |
| APIC REST API | Python scripts for health checks and fabric inventory |
| ServiceNow integration | Full CR lifecycle: New → Assess → Authorize → Scheduled → Implement → Review → Closed |
| Testing | `ansible-lint`, `terraform validate`, `pytest` with mocked APIC responses |

---

## Repository structure

```
aci-gitops-pipeline/
├── .github/
│   ├── CODEOWNERS                  # Repository ownership
│   └── workflows/
│       ├── validate.yml            # PR: lint + plan + dry-run
│       └── deploy.yml              # Merge to main: deploy + health check
│
├── tenants/                        # Source of truth — edit these files
│   ├── dev-tenant.yml              # Dev environment (unenforced VRF)
│   └── prod-tenant.yml             # Prod environment (3-tier app model)
│
├── ansible/
│   ├── playbooks/
│   │   ├── provision_tenant.yml    # Main provisioning playbook
│   │   ├── validate_fabric.yml     # Read-only fabric health check
│   │   └── rollback.yml            # Remove tenant objects from APIC
│   ├── roles/
│   │   ├── aci_tenant/             # Tenant / VRF / BD provisioning
│   │   ├── aci_networking/         # App profile / EPG provisioning
│   │   └── aci_security/           # Contracts / filters / subjects
│   ├── inventory/
│   │   ├── hosts.yml               # APIC inventory
│   │   └── group_vars/
│   │       └── aci.yml             # ACI connection variables
│   ├── ansible.cfg
│   ├── ansible-requirements.yml
│   └── .ansible-lint
│
├── terraform/
│   ├── modules/
│   │   └── tenant/                 # Reusable ACI tenant module
│   └── environments/
│       └── dev/                    # Dev APIC target
│
├── python/
│   ├── apic_health.py              # Post-deploy fabric health check
│   ├── servicenow_cr.py            # Full CR lifecycle automation
│   └── tests/
│       └── test_apic_health.py     # pytest with mocked APIC
│
├── Makefile                        # Local dev shortcuts
├── requirements.txt
└── .env.example                    # Credential template
```

---

## Quick start

### Prerequisites

- Python 3.10+
- Ansible 8+
- Terraform 1.6+
- Access to [Cisco DevNet always-on ACI sandbox](https://devnetsandbox.cisco.com) or your own APIC
- ServiceNow developer instance at [developer.servicenow.com](https://developer.servicenow.com)

### 1. Clone and install dependencies

```bash
git clone https://github.com/imrankhanbit/aci-gitops-pipeline.git
cd aci-gitops-pipeline

pip install -r requirements.txt
ansible-galaxy collection install -r ansible/ansible-requirements.yml
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your sandbox credentials
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
  app_profiles:
    - name: "PROD-APP"
      epgs:
        - name: "WEB-EPG"
          bd: "PROD-BD"
          contracts:
            provided: ["WEB-TO-APP"]
            consumed: []
  contracts:
    - name: "WEB-TO-APP"
      scope: tenant
      subjects:
        - name: "HTTP-HTTPS"
          filters: ["HTTPS-FILTER"]
```

### 4. Run the pipeline locally

```bash
# Validate and dry-run (safe — no changes to APIC)
make validate TENANT=tenants/dev-tenant.yml

# Deploy to APIC
make deploy TENANT=tenants/dev-tenant.yml

# Post-deploy health check
make health-check TENANT=tenants/dev-tenant.yml

# Roll back last deployment
make rollback TENANT=tenants/dev-tenant.yml
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

| Secret | Description |
|---|---|
| `APIC_HOST` | APIC hostname (e.g. `sandboxapicdc.cisco.com`) |
| `APIC_USERNAME` | APIC username |
| `APIC_PASSWORD` | APIC password |
| `SNOW_INSTANCE` | ServiceNow instance URL (e.g. `https://dev12345.service-now.com`) |
| `SNOW_USERNAME` | ServiceNow username |
| `SNOW_PASSWORD` | ServiceNow password |
| `SNOW_ASSIGNMENT_GROUP` | Assignment group name (e.g. `Network`) |

---

## Pipeline stages

### On Pull Request (`validate.yml`)

| Stage | Tool | What it checks |
|---|---|---|
| Ansible lint | `ansible-lint` | Playbook best practices (moderate profile) |
| Terraform validate & plan | `terraform validate` + `plan` | HCL syntax and planned changes |
| Python unit tests | `pytest` | Script logic with mocked APIC responses |
| Ansible dry-run | `--check --diff` | APIC change preview against DevNet sandbox |

### On Merge to main (`deploy.yml`)

| Stage | What happens |
|---|---|
| Open ServiceNow CR | Auto-created; walks New → Assess → Authorize → CAB approval → Scheduled |
| Ansible deploy | Tenant objects pushed to APIC via `cisco.aci` collection |
| Health check | Python verifies tenant, VRF, and BD exist in APIC |
| Close ServiceNow CR | CR walked Implement → Review → Closed with pipeline evidence |

---

## ServiceNow CR lifecycle

The pipeline fully automates the ITIL change request lifecycle — no human intervention required:

```
New → Assess → Authorize → [CAB auto-approve] → Scheduled
                                                      ↓
                                              [Ansible deploy]
                                                      ↓
                                              [Health check]
                                                      ↓
                                        Implement → Review → Closed
                                        (close_code: successful)
```

The `sn_chg_rest` Change Management REST API is used for state transitions, which correctly integrates with the ServiceNow change model state machine.

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

`Cisco ACI` · `Ansible` · `cisco.aci collection` · `Terraform` · `CiscoDevNet/aci provider` · `Python` · `GitHub Actions` · `ServiceNow REST API` · `sn_chg_rest API` · `APIC REST API` · `YAML` · `Jinja2` · `pytest` · `ansible-lint`

---

## Author

Built by a Senior Network Automation Engineer with 14 years of DC infrastructure experience, specialising in Cisco ACI and network automation at scale.
