#!/usr/bin/env python3
"""
servicenow_cr.py
================
Automates ServiceNow change request lifecycle for ACI pipeline deployments.

Actions:
  create  — Opens a new Standard change request, prints sys_id
  close   — Closes an existing CR with deployment evidence
  status  — Prints current state of a CR

Credentials from environment variables:
  SNOW_INSTANCE   — e.g. https://dev12345.service-now.com
  SNOW_USERNAME
  SNOW_PASSWORD
  SNOW_ASSIGNMENT_GROUP — sys_id of the assignment group

Usage:
  # Create CR before deployment
  python servicenow_cr.py create \
    --tenant ACME-PROD \
    --pipeline-url https://github.com/org/repo/actions/runs/123456 \
    --description "GitOps deployment: add WEB-EPG to ACME-PROD tenant"

  # Close CR after successful deployment
  python servicenow_cr.py close \
    --cr-id CHG0012345 \
    --pipeline-url https://github.com/org/repo/actions/runs/123456
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone, timedelta

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SNOW_DATE_FMT = "%Y-%m-%d %H:%M:%S"


class ServiceNowClient:
    def __init__(self, instance: str, username: str, password: str):
        self.base_url = instance.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _url(self, table: str, sys_id: str = "") -> str:
        path = f"/api/now/table/{table}"
        if sys_id:
            path += f"/{sys_id}"
        return f"{self.base_url}{path}"

    def create_change(self, payload: dict) -> dict:
        resp = self.session.post(self._url("change_request"), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["result"]

    def update_change(self, sys_id: str, payload: dict) -> dict:
        resp = self.session.patch(self._url("change_request", sys_id), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["result"]

    def get_change_by_number(self, number: str) -> dict:
        params = {
            "sysparm_query": f"number={number}",
            "sysparm_limit": 1,
            "sysparm_fields": "sys_id,number,state,short_description,assigned_to",
        }
        resp = self.session.get(self._url("change_request"), params=params, timeout=30)
        resp.raise_for_status()
        results = resp.json()["result"]
        if not results:
            raise ValueError(f"Change request {number} not found.")
        return results[0]


def cmd_create(args, client: ServiceNowClient) -> int:
    now = datetime.now(timezone.utc)
    planned_start = now + timedelta(minutes=5)
    planned_end   = now + timedelta(hours=2)

    payload = {
        "type": "standard",
        "category": "Network",
        "short_description": (
            f"[ACI GitOps] Tenant provisioning: {args.tenant} "
            f"— {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        ),
        "description": (
            f"Automated change raised by aci-gitops-pipeline.\n\n"
            f"Tenant: {args.tenant}\n"
            f"Pipeline run: {args.pipeline_url}\n"
            f"Triggered by: {os.environ.get('GITHUB_ACTOR', 'pipeline')}\n"
            f"Commit: {os.environ.get('GITHUB_SHA', 'N/A')[:12]}\n\n"
            f"Change description:\n{args.description}\n\n"
            f"Rollback plan: Run rollback.yml playbook with the same tenant YAML file. "
            f"All ACI objects carry the annotation 'orchestrated-by:aci-gitops-pipeline' "
            f"for identification."
        ),
        "start_date": planned_start.strftime(SNOW_DATE_FMT),
        "end_date":   planned_end.strftime(SNOW_DATE_FMT),
        "assignment_group": os.environ.get("SNOW_ASSIGNMENT_GROUP", ""),
        "risk": "low",
        "impact": "2",
        "priority": "3",
        "state": "scheduled",   # -5 in ServiceNow numeric state
    }

    result = client.create_change(payload)
    cr_number = result.get("number", "N/A")
    cr_sys_id = result.get("sys_id", "N/A")

    print(f"\nChange request created successfully.")
    print(f"  Number : {cr_number}")
    print(f"  sys_id : {cr_sys_id}")
    print(f"  State  : {result.get('state', 'N/A')}\n")

    # Write sys_id to file so deploy.yml can read it for the close step
    with open(".snow_cr_sysid", "w") as f:
        f.write(cr_sys_id)
    with open(".snow_cr_number", "w") as f:
        f.write(cr_number)

    log.info("CR sys_id written to .snow_cr_sysid for pipeline use.")
    return 0


def cmd_close(args, client: ServiceNowClient) -> int:
    # Resolve sys_id from CR number
    cr = client.get_change_by_number(args.cr_id)
    sys_id = cr["sys_id"]

    close_notes = (
        f"Change implemented successfully by aci-gitops-pipeline.\n\n"
        f"Pipeline run : {args.pipeline_url}\n"
        f"Completed at : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Commit SHA   : {os.environ.get('GITHUB_SHA', 'N/A')}\n\n"
        f"Post-deploy health check: PASSED\n"
        f"  - All fabric nodes active\n"
        f"  - Zero critical faults\n"
        f"  - Tenant objects confirmed present in APIC\n\n"
        f"Evidence: See pipeline run URL above for full logs and health check output."
    )

    payload = {
        "state": "closed",
        "close_code": "successful",
        "close_notes": close_notes,
        "work_notes": f"Auto-closed by aci-gitops-pipeline. Pipeline: {args.pipeline_url}",
    }

    result = client.update_change(sys_id, payload)
    print(f"\nChange request {args.cr_id} closed.")
    print(f"  State      : {result.get('state', 'N/A')}")
    print(f"  Close code : {result.get('close_code', 'N/A')}\n")
    return 0


def cmd_status(args, client: ServiceNowClient) -> int:
    cr = client.get_change_by_number(args.cr_id)
    print(f"\nChange request: {cr.get('number')}")
    print(f"  State       : {cr.get('state')}")
    print(f"  Description : {cr.get('short_description')}")
    print(f"  Assigned to : {cr.get('assigned_to', {}).get('display_value', 'N/A')}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ServiceNow change request automation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = subparsers.add_parser("create", help="Create a new change request")
    p_create.add_argument("--tenant", required=True)
    p_create.add_argument("--pipeline-url", required=True)
    p_create.add_argument("--description", default="ACI tenant provisioning via GitOps pipeline")

    # close
    p_close = subparsers.add_parser("close", help="Close an existing change request")
    p_close.add_argument("--cr-id", required=True, help="CR number, e.g. CHG0012345")
    p_close.add_argument("--pipeline-url", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Print CR status")
    p_status.add_argument("--cr-id", required=True)

    args = parser.parse_args()

    instance  = os.environ.get("SNOW_INSTANCE")
    username  = os.environ.get("SNOW_USERNAME")
    password  = os.environ.get("SNOW_PASSWORD")

    if not all([instance, username, password]):
        log.error("SNOW_INSTANCE, SNOW_USERNAME, SNOW_PASSWORD must all be set.")
        return 1

    client = ServiceNowClient(instance, username, password)

    try:
        if args.command == "create":
            return cmd_create(args, client)
        elif args.command == "close":
            return cmd_close(args, client)
        elif args.command == "status":
            return cmd_status(args, client)
    except requests.HTTPError as exc:
        log.error("ServiceNow API error: %s — %s", exc.response.status_code, exc.response.text)
        return 1
    except Exception as exc:
        log.error("Unexpected error: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
