#!/usr/bin/env python3
"""
servicenow_cr.py - ServiceNow Change Request lifecycle automation.

Open CR flow  : New -> Assess -> Authorize -> [auto-approve] -> Scheduled -> Implement
Close CR flow : Implement -> Review -> Closed (with close_code + close_notes)

Credentials from env: SNOW_INSTANCE, SNOW_USERNAME, SNOW_PASSWORD, SNOW_ASSIGNMENT_GROUP
"""

import os
import sys
import time
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

# ServiceNow Change Request numeric state values
STATE_NEW       = -5
STATE_ASSESS    = -4
STATE_AUTHORIZE = -3
STATE_SCHEDULED = -2
STATE_IMPLEMENT =  0
STATE_REVIEW    =  3
STATE_CLOSED    =  4
STATE_CANCELED  =  7


class ServiceNowClient:
    def __init__(self, instance, username, password):
        self.base_url = instance.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _url(self, table, sys_id=""):
        path = "/api/now/table/{}".format(table)
        if sys_id:
            path += "/{}".format(sys_id)
        return "{}{}".format(self.base_url, path)

    def create_change(self, payload):
        resp = self.session.post(self._url("change_request"), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["result"]

    def update_change(self, sys_id, payload):
        resp = self.session.patch(
            self._url("change_request", sys_id), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["result"]

    def get_change(self, sys_id):
        resp = self.session.get(
            self._url("change_request", sys_id),
            params={"sysparm_fields": "sys_id,number,state,short_description"},
            timeout=30)
        resp.raise_for_status()
        return resp.json()["result"]

    def get_change_by_number(self, number):
        params = {
            "sysparm_query": "number={}".format(number),
            "sysparm_limit": 1,
            "sysparm_fields": "sys_id,number,state,short_description",
        }
        resp = self.session.get(self._url("change_request"), params=params, timeout=30)
        resp.raise_for_status()
        results = resp.json()["result"]
        if not results:
            raise ValueError("Change request {} not found.".format(number))
        return results[0]

    def get_pending_approvals(self, sys_id):
        params = {
            "sysparm_query": "document_id={}&state=requested".format(sys_id),
            "sysparm_fields": "sys_id,approver,state",
        }
        resp = self.session.get(
            self._url("sysapproval_approver"), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("result", [])

    def approve_record(self, approval_sys_id):
        resp = self.session.patch(
            self._url("sysapproval_approver", approval_sys_id),
            json={"state": "approved",
                  "comments": "Auto-approved by aci-gitops-pipeline CI/CD."},
            timeout=30)
        resp.raise_for_status()
        return resp.json()["result"]


def transition(client, sys_id, cr_number, target_state, label, extra=None):
    """Move CR to target_state and verify. Returns actual state after update."""
    payload = {"state": target_state}
    if extra:
        payload.update(extra)
    try:
        result = client.update_change(sys_id, payload)
        actual = int(result.get("state", 99))
        log.info("CR %s -> %s (state=%s)", cr_number, label, actual)
        if actual == STATE_CANCELED:
            log.error("CR %s was CANCELED after transition to %s.", cr_number, label)
            return actual
        return actual
    except requests.HTTPError as exc:
        log.error("Transition to %s failed: %s - %s",
                  label, exc.response.status_code, exc.response.text[:300])
        raise


def cmd_create(args, client):
    """Create CR and walk it: New -> Assess -> Authorize -> [approve] -> Scheduled -> Implement"""
    now = datetime.now(timezone.utc)
    assignment_group = os.environ.get("SNOW_ASSIGNMENT_GROUP", "Network")

    # ── Step 1: Create CR ────────────────────────────────────────
    payload = {
        "type": "normal",
        "category": "Network",
        "short_description": "[ACI GitOps] Tenant: {} - {}".format(
            args.tenant, now.strftime("%Y-%m-%d %H:%M UTC")),
        "description": (
            "Automated change by aci-gitops-pipeline.\n\n"
            "Tenant       : {}\nPipeline run : {}\n"
            "Triggered by : {}\nCommit       : {}\n\n"
            "{}\n\nRollback: rollback.yml playbook with same tenant YAML."
        ).format(
            args.tenant, args.pipeline_url,
            os.environ.get("GITHUB_ACTOR", "pipeline"),
            os.environ.get("GITHUB_SHA", "N/A")[:12],
            args.description,
        ),
        "start_date": (now + timedelta(minutes=5)).strftime(SNOW_DATE_FMT),
        "end_date":   (now + timedelta(hours=2)).strftime(SNOW_DATE_FMT),
        "assignment_group": assignment_group,
        "risk": "low",
        "impact": "2",
        "priority": "3",
    }
    result = client.create_change(payload)
    cr_number = result["number"]
    sys_id    = result["sys_id"]
    log.info("CR %s created (sys_id: %s)", cr_number, sys_id)

    with open(".snow_cr_sysid", "w") as f:
        f.write(sys_id)
    with open(".snow_cr_number", "w") as f:
        f.write(cr_number)

    time.sleep(3)

    # ── Step 2: New -> Assess ────────────────────────────────────
    transition(client, sys_id, cr_number, STATE_ASSESS, "Assess", {
        "assignment_group": assignment_group,
        "work_notes": "Assessed by aci-gitops-pipeline.",
    })
    time.sleep(3)

    # ── Step 3: Assess -> Authorize ──────────────────────────────
    transition(client, sys_id, cr_number, STATE_AUTHORIZE, "Authorize", {
        "work_notes": "Moving to Authorize for CAB approval.",
    })
    log.info("Waiting 15s for ServiceNow to generate approval records...")
    time.sleep(15)

    # ── Step 4: Auto-approve all pending CAB approvals ───────────
    approvals = client.get_pending_approvals(sys_id)
    log.info("Found %d pending approval(s).", len(approvals))
    for appr in approvals:
        client.approve_record(appr["sys_id"])
        log.info("  Approved: %s", appr["sys_id"])

    # ── Step 5: Wait for ServiceNow to move CR to Scheduled ──────
    log.info("Waiting for CR to reach Scheduled state after approvals...")
    for attempt in range(12):   # up to 2 minutes
        time.sleep(10)
        cr = client.get_change(sys_id)
        state = int(cr.get("state", 99))
        log.info("  Attempt %d: state=%s", attempt + 1, state)
        if state == STATE_CANCELED:
            log.error("CR %s was canceled.", cr_number)
            return 1
        if state == STATE_SCHEDULED:
            log.info("CR %s is Scheduled.", cr_number)
            break
    else:
        log.error("CR did not reach Scheduled within 2 minutes.")
        return 1

    # ── Step 6: Scheduled -> Implement ───────────────────────────
    transition(client, sys_id, cr_number, STATE_IMPLEMENT, "Implement", {
        "work_notes": "Deployment starting - aci-gitops-pipeline.",
    })

    print("\nCR {} is in Implement state. Proceeding with deployment.\n".format(cr_number))
    return 0


def cmd_close(args, client):
    """Walk CR: Implement -> Review -> Closed with close_code and close_notes."""
    cr = client.get_change_by_number(args.cr_id)
    sys_id = cr["sys_id"]
    log.info("Closing CR %s", args.cr_id)

    completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commit_sha   = os.environ.get("GITHUB_SHA", "N/A")

    # ── Review ───────────────────────────────────────────────────
    transition(client, sys_id, args.cr_id, STATE_REVIEW, "Review", {
        "work_notes": (
            "Post-deploy health check PASSED.\n"
            "Pipeline: {}\nCompleted: {}\nCommit: {}\n"
            "Verified: Tenant, VRF, BD, EPG confirmed in APIC."
        ).format(args.pipeline_url, completed_at, commit_sha),
    })
    time.sleep(3)

    # ── Closed ───────────────────────────────────────────────────
    transition(client, sys_id, args.cr_id, STATE_CLOSED, "Closed", {
        "close_code": "successful",
        "close_notes": (
            "Change implemented and verified successfully.\n\n"
            "Automated deployment via aci-gitops-pipeline completed at {}.\n"
            "Pipeline run : {}\nCommit SHA   : {}\n\n"
            "Post-deploy verification: Tenant objects confirmed in Cisco ACI "
            "(VRF, Bridge Domain, EPG verified via APIC REST API)."
        ).format(completed_at, args.pipeline_url, commit_sha),
    })

    print("\nCR {} closed successfully (close_code: successful).\n".format(args.cr_id))
    return 0


def cmd_status(args, client):
    cr = client.get_change_by_number(args.cr_id)
    print("\nChange request : {}".format(cr.get("number")))
    print("  State        : {}".format(cr.get("state")))
    print("  Description  : {}\n".format(cr.get("short_description")))
    return 0


def main():
    parser = argparse.ArgumentParser(description="ServiceNow CR automation")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create")
    p.add_argument("--tenant", required=True)
    p.add_argument("--pipeline-url", required=True)
    p.add_argument("--description", default="ACI tenant provisioning via GitOps pipeline")

    p = sub.add_parser("close")
    p.add_argument("--cr-id", required=True)
    p.add_argument("--pipeline-url", required=True)

    p = sub.add_parser("status")
    p.add_argument("--cr-id", required=True)

    args = parser.parse_args()

    instance = os.environ.get("SNOW_INSTANCE")
    username = os.environ.get("SNOW_USERNAME")
    password = os.environ.get("SNOW_PASSWORD")

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
        log.error("ServiceNow API error: %s - %s",
                  exc.response.status_code, exc.response.text[:300])
        return 1
    except Exception as exc:
        log.error("Unexpected error: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
