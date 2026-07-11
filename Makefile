# ============================================================
# Makefile — Local development shortcuts
# Usage: make <target> [TENANT=tenants/prod-tenant.yml]
# ============================================================

TENANT       ?= tenants/dev-tenant.yml
TARGET_HOST  ?= devnet-sandbox
ANSIBLE_DIR  := ansible
TF_ENV       ?= dev

.PHONY: help install validate deploy rollback health-check test lint tf-plan tf-apply

help:
	@echo ""
	@echo "  ACI GitOps Pipeline — Makefile"
	@echo "  ================================"
	@echo "  install       Install all dependencies"
	@echo "  validate      Lint + dry-run (safe, no APIC changes)"
	@echo "  deploy        Deploy tenant to APIC"
	@echo "  rollback      Remove tenant from APIC"
	@echo "  health-check  Run post-deploy APIC health check"
	@echo "  test          Run Python unit tests"
	@echo "  lint          Run ansible-lint + yamllint"
	@echo "  tf-plan       Terraform plan (dev env)"
	@echo "  tf-apply      Terraform apply (dev env)"
	@echo ""
	@echo "  Variables:"
	@echo "    TENANT=$(TENANT)"
	@echo "    TARGET_HOST=$(TARGET_HOST)"
	@echo "    TF_ENV=$(TF_ENV)"
	@echo ""

install:
	pip install -r requirements.txt
	ansible-galaxy collection install -r $(ANSIBLE_DIR)/ansible-requirements.yml

lint:
	yamllint tenants/ $(ANSIBLE_DIR)/
	ansible-lint $(ANSIBLE_DIR)/playbooks/ $(ANSIBLE_DIR)/roles/

test:
	pytest python/tests/ -v --cov=python --cov-report=term-missing

validate: lint test
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/provision_tenant.yml \
		-i inventory/hosts.yml \
		-e "tenant_file=../../$(TENANT)" \
		-e "target_host=$(TARGET_HOST)" \
		--check --diff -v

deploy:
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/provision_tenant.yml \
		-i inventory/hosts.yml \
		-e "tenant_file=../../$(TENANT)" \
		-e "target_host=$(TARGET_HOST)" \
		-v

rollback:
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/rollback.yml \
		-i inventory/hosts.yml \
		-e "tenant_file=../../$(TENANT)" \
		-e "target_host=$(TARGET_HOST)" \
		-v

health-check:
	@TENANT_NAME=$$(python3 -c "import yaml; d=yaml.safe_load(open('$(TENANT)')); print(d['tenant']['name'])") && \
	python python/apic_health.py --tenant $$TENANT_NAME

tf-plan:
	cd terraform/environments/$(TF_ENV) && terraform init -input=false && terraform plan -input=false

tf-apply:
	cd terraform/environments/$(TF_ENV) && terraform apply -input=false -auto-approve
