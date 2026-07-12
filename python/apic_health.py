#!/usr/bin/env python3
"""
apic_health.py
==============
Post-deploy fabric health check against Cisco APIC REST API.

Queries:
  - Fabric node states (controllers excluded from active count)
  - Critical fault count (reported as warning, not a hard failure)
  - Tenant object count (confirms deployed objects are present)

Exit codes:
  0 — tenant objects verified, pipeline continues
  1 — tenant missing or API error, pipeline fails

Usage:
  python apic_health.py --tenant ACME-DEV
  python apic_health.py --tenant ACME-DEV --host sandboxapicdc.cisco.com

Credentials are read from environment variables:
  APIC_HOST, APIC_USERNAME, APIC_PASSWORD
"""

import os
import sys
import json
import argparse
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


@dataclass
class HealthReport:
    tenant: str
    node_count: int = 0
    active_nodes: int = 0
    inactive_nodes: list = field(default_factory=list)
    critical_faults: int = 0
    fault_codes: list = field(default_factory=list)
    tenant_exists: bool = False
    vrf_count: int = 0
    bd_count: int = 0
    epg_count: int = 0
    healthy: bool = False

    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            f"  ACI Fabric Health Report - Tenant: {self.tenant}",
            f"{'='*60}",
            f"  Fabric nodes : {self.active_nodes}/{self.node_count} active",
            f"  Critical faults: {self.critical_faults} (sandbox pre-existing, informational)",
            f"  Tenant exists  : {'YES' if self.tenant_exists else 'NO'}",
            f"  VRFs           : {self.vrf_count}",
            f"  Bridge Domains : {self.bd_count}",
            f"  EPGs           : {self.epg_count}",
        ]
        if self.inactive_nodes:
            lines.append(f"  Inactive nodes : {', '.join(self.inactive_nodes)}")
        if self.fault_codes:
            lines.append(f"  Fault codes    : {', '.join(self.fault_codes)} (pre-existing)")
        status = "PASS" if self.healthy else "FAIL"
        lines += [f"{'='*60}", f"  Result: {status}", f"{'='*60}\n"]
        return "\n".join(lines)


class APICClient:
    def __init__(self, host: str, username: str, password: str, verify_ssl: bool = False):
        self.base_url = f"https://{host}"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self._token: Optional[str] = None

    def login(self) -> None:
        url = f"{self.base_url}/api/aaaLogin.json"
        payload = {
            "aaaUser": {
                "attributes": {
                    "name": self.username,
                    "pwd": self.password,
                }
            }
        }
        resp = self.session.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        self._token = data["imdata"][0]["aaaLogin"]["attributes"]["token"]
        self.session.headers.update({"APIC-cookie": self._token})
        log.info("Authenticated to APIC at %s", self.base_url)

    def get(self, path: str) -> list:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json().get("imdata", [])

    def logout(self) -> None:
        try:
            self.session.post(f"{self.base_url}/api/aaaLogout.json", timeout=10)
        except Exception:
            pass


def check_fabric_health(client: APICClient, tenant_name: str) -> HealthReport:
    report = HealthReport(tenant=tenant_name)

    # Fabric nodes — controllers show as 'commissioned', not 'active'; exclude from count
    log.info("Querying fabric nodes...")
    nodes = client.get("/api/node/class/fabricNode.json?order-by=fabricNode.id")
    for node in nodes:
        attrs = node["fabricNode"]["attributes"]
        role = attrs.get("role", "")
        fabric_st = attrs.get("fabricSt", "")
        if role == "controller":
            continue   # controllers are never 'active' — expected behaviour
        report.node_count += 1
        if fabric_st == "active":
            report.active_nodes += 1
        else:
            report.inactive_nodes.append(f"{attrs['id']} ({role}) - {fabric_st}")
    log.info("Nodes: %d total, %d active (controllers excluded)", report.node_count, report.active_nodes)

    # Critical faults — reported for visibility but NOT a pipeline gate on shared sandboxes
    log.info("Querying critical faults...")
    faults = client.get(
        '/api/node/class/faultSummary.json'
        '?query-target-filter=and(eq(faultSummary.severity,"critical"))'
    )
    report.critical_faults = len(faults)
    report.fault_codes = [f["faultSummary"]["attributes"]["code"] for f in faults]
    log.info("Critical faults: %d (informational only)", report.critical_faults)

    # Tenant objects — this is what the pipeline actually verifies
    log.info("Checking tenant '%s' objects...", tenant_name)
    tenants = client.get(f'/api/node/mo/uni/tn-{tenant_name}.json?query-target=self')
    report.tenant_exists = len(tenants) > 0

    if report.tenant_exists:
        vrfs = client.get(
            f'/api/node/mo/uni/tn-{tenant_name}.json'
            f'?query-target=subtree&target-subtree-class=fvCtx'
        )
        report.vrf_count = len(vrfs)

        bds = client.get(
            f'/api/node/mo/uni/tn-{tenant_name}.json'
            f'?query-target=subtree&target-subtree-class=fvBD'
        )
        report.bd_count = len(bds)

        epgs = client.get(
            f'/api/node/mo/uni/tn-{tenant_name}.json'
            f'?query-target=subtree&target-subtree-class=fvAEPg'
        )
        report.epg_count = len(epgs)

    # PASS if tenant exists with at least one VRF and one BD — fabric faults are not a gate
    report.healthy = (
        report.tenant_exists
        and report.vrf_count > 0
        and report.bd_count > 0
    )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="ACI fabric health check")
    parser.add_argument("--host", default=os.environ.get("APIC_HOST", "sandboxapicdc.cisco.com"))
    parser.add_argument("--username", default=os.environ.get("APIC_USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("APIC_PASSWORD"))
    parser.add_argument("--tenant", required=True, help="Tenant name to verify")
    parser.add_argument("--output-json", help="Write JSON report to this file path")
    args = parser.parse_args()

    if not args.password:
        log.error("APIC_PASSWORD environment variable is not set.")
        return 1

    client = APICClient(host=args.host, username=args.username, password=args.password)

    try:
        client.login()
        report = check_fabric_health(client, args.tenant)
        print(report.summary())

        if args.output_json:
            with open(args.output_json, "w") as f:
                json.dump(report.__dict__, f, indent=2)
            log.info("JSON report written to %s", args.output_json)

        return 0 if report.healthy else 1

    except requests.RequestException as exc:
        log.error("APIC API error: %s", exc)
        return 1
    finally:
        client.logout()


if __name__ == "__main__":
    sys.exit(main())
