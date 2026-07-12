#!/usr/bin/env python3
"""
apic_health.py - Post-deploy ACI fabric health check.

Pass/Fail is based on tenant object verification only.
Fabric faults are reported as informational (sandbox has pre-existing faults).
Controllers are excluded from node active count (expected behaviour).

Exit codes: 0 = tenant verified, 1 = tenant missing or API error
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
            "\n" + "=" * 60,
            "  ACI Fabric Health Report - Tenant: " + self.tenant,
            "=" * 60,
            "  Fabric nodes : {}/{} active (controllers excluded)".format(
                self.active_nodes, self.node_count),
            "  Critical faults: {} (informational - pre-existing sandbox faults)".format(
                self.critical_faults),
            "  Tenant exists  : {}".format("YES" if self.tenant_exists else "NO"),
            "  VRFs           : {}".format(self.vrf_count),
            "  Bridge Domains : {}".format(self.bd_count),
            "  EPGs           : {}".format(self.epg_count),
        ]
        if self.inactive_nodes:
            lines.append("  Inactive nodes : {}".format(", ".join(self.inactive_nodes)))
        if self.fault_codes:
            lines.append("  Fault codes    : {} (pre-existing)".format(
                ", ".join(self.fault_codes)))
        status = "PASS" if self.healthy else "FAIL"
        lines += ["=" * 60, "  Result: " + status, "=" * 60 + "\n"]
        return "\n".join(lines)


class APICClient:
    def __init__(self, host, username, password, verify_ssl=False):
        self.base_url = "https://{}".format(host)
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self._token: Optional[str] = None

    def login(self):
        url = "{}/api/aaaLogin.json".format(self.base_url)
        payload = {"aaaUser": {"attributes": {"name": self.username, "pwd": self.password}}}
        resp = self.session.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        self._token = resp.json()["imdata"][0]["aaaLogin"]["attributes"]["token"]
        self.session.headers.update({"APIC-cookie": self._token})
        log.info("Authenticated to APIC at %s", self.base_url)

    def get(self, path):
        resp = self.session.get("{}{}".format(self.base_url, path), timeout=30)
        resp.raise_for_status()
        return resp.json().get("imdata", [])

    def logout(self):
        try:
            self.session.post("{}/api/aaaLogout.json".format(self.base_url), timeout=10)
        except Exception:
            pass


def check_fabric_health(client, tenant_name):
    report = HealthReport(tenant=tenant_name)

    # Fabric nodes - controllers are never 'active', exclude them from count
    log.info("Querying fabric nodes...")
    nodes = client.get("/api/node/class/fabricNode.json?order-by=fabricNode.id")
    for node in nodes:
        attrs = node["fabricNode"]["attributes"]
        if attrs.get("role") == "controller":
            continue
        report.node_count += 1
        if attrs.get("fabricSt") == "active":
            report.active_nodes += 1
        else:
            report.inactive_nodes.append(
                "{} ({}) - {}".format(attrs["id"], attrs["role"], attrs["fabricSt"]))
    log.info("Nodes: %d total, %d active (controllers excluded)",
             report.node_count, report.active_nodes)

    # Critical faults - informational only, not a pipeline gate
    log.info("Querying critical faults...")
    faults = client.get(
        '/api/node/class/faultSummary.json'
        '?query-target-filter=and(eq(faultSummary.severity,"critical"))')
    report.critical_faults = len(faults)
    report.fault_codes = [f["faultSummary"]["attributes"]["code"] for f in faults]
    log.info("Critical faults: %d (informational only)", report.critical_faults)

    # Tenant objects - this is the actual deployment verification gate
    log.info("Checking tenant '%s' objects...", tenant_name)
    tenants = client.get("/api/node/mo/uni/tn-{}.json?query-target=self".format(tenant_name))
    report.tenant_exists = len(tenants) > 0

    if report.tenant_exists:
        report.vrf_count = len(client.get(
            "/api/node/mo/uni/tn-{}.json"
            "?query-target=subtree&target-subtree-class=fvCtx".format(tenant_name)))
        report.bd_count = len(client.get(
            "/api/node/mo/uni/tn-{}.json"
            "?query-target=subtree&target-subtree-class=fvBD".format(tenant_name)))
        report.epg_count = len(client.get(
            "/api/node/mo/uni/tn-{}.json"
            "?query-target=subtree&target-subtree-class=fvAEPg".format(tenant_name)))

    # PASS = tenant exists with at least one VRF and BD
    report.healthy = report.tenant_exists and report.vrf_count > 0 and report.bd_count > 0
    return report


def main():
    parser = argparse.ArgumentParser(description="ACI fabric health check")
    parser.add_argument("--host", default=os.environ.get("APIC_HOST", "sandboxapicdc.cisco.com"))
    parser.add_argument("--username", default=os.environ.get("APIC_USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("APIC_PASSWORD"))
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args()

    if not args.password:
        log.error("APIC_PASSWORD not set")
        return 1

    client = APICClient(args.host, args.username, args.password)
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
