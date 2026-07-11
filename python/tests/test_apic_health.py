"""
test_apic_health.py
===================
Unit tests for apic_health.py.
Uses unittest.mock to avoid any real APIC calls — safe to run in CI.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from apic_health import APICClient, HealthReport, check_fabric_health


# ── Fixtures ─────────────────────────────────────────────────────────────────

MOCK_NODES_ALL_ACTIVE = [
    {"fabricNode": {"attributes": {"id": "101", "role": "leaf",   "fabricSt": "active"}}},
    {"fabricNode": {"attributes": {"id": "102", "role": "leaf",   "fabricSt": "active"}}},
    {"fabricNode": {"attributes": {"id": "201", "role": "spine",  "fabricSt": "active"}}},
]

MOCK_NODES_ONE_INACTIVE = [
    {"fabricNode": {"attributes": {"id": "101", "role": "leaf",   "fabricSt": "active"}}},
    {"fabricNode": {"attributes": {"id": "102", "role": "leaf",   "fabricSt": "inactive"}}},
    {"fabricNode": {"attributes": {"id": "201", "role": "spine",  "fabricSt": "active"}}},
]

MOCK_NO_FAULTS = []

MOCK_CRITICAL_FAULTS = [
    {"faultSummary": {"attributes": {"code": "F0123", "severity": "critical"}}},
    {"faultSummary": {"attributes": {"code": "F0456", "severity": "critical"}}},
]

MOCK_TENANT_EXISTS = [
    {"fvTenant": {"attributes": {"name": "ACME-PROD", "dn": "uni/tn-ACME-PROD"}}}
]

MOCK_TENANT_NOT_FOUND = []

MOCK_VRFS = [{"fvCtx": {"attributes": {"name": "PROD-VRF"}}}]
MOCK_BDS  = [
    {"fvBD": {"attributes": {"name": "WEB-BD"}}},
    {"fvBD": {"attributes": {"name": "APP-BD"}}},
]
MOCK_EPGS = [
    {"fvAEPg": {"attributes": {"name": "WEB-EPG"}}},
    {"fvAEPg": {"attributes": {"name": "APP-EPG"}}},
    {"fvAEPg": {"attributes": {"name": "DB-EPG"}}},
]


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestHealthReport(unittest.TestCase):

    def test_healthy_report_summary_contains_pass(self):
        report = HealthReport(
            tenant="ACME-PROD",
            node_count=3,
            active_nodes=3,
            critical_faults=0,
            tenant_exists=True,
            vrf_count=1,
            bd_count=2,
            epg_count=3,
            healthy=True,
        )
        self.assertIn("PASS", report.summary())
        self.assertIn("ACME-PROD", report.summary())

    def test_unhealthy_report_summary_contains_fail(self):
        report = HealthReport(tenant="ACME-PROD", healthy=False)
        self.assertIn("FAIL", report.summary())

    def test_inactive_nodes_appear_in_summary(self):
        report = HealthReport(
            tenant="ACME-PROD",
            inactive_nodes=["102 (leaf) — inactive"],
            healthy=False,
        )
        self.assertIn("102", report.summary())

    def test_fault_codes_appear_in_summary(self):
        report = HealthReport(
            tenant="ACME-PROD",
            fault_codes=["F0123", "F0456"],
            healthy=False,
        )
        self.assertIn("F0123", report.summary())


class TestAPICClientGet(unittest.TestCase):

    def _make_client(self):
        client = APICClient("sandboxapicdc.cisco.com", "admin", "password")
        client.session = MagicMock()
        client._token = "mock-token"
        return client

    def test_get_returns_imdata_list(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"imdata": MOCK_NODES_ALL_ACTIVE}
        mock_resp.raise_for_status = MagicMock()
        client.session.get.return_value = mock_resp

        result = client.get("/api/node/class/fabricNode.json")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["fabricNode"]["attributes"]["id"], "101")

    def test_get_raises_on_http_error(self):
        import requests
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        client.session.get.return_value = mock_resp

        with self.assertRaises(requests.HTTPError):
            client.get("/api/node/class/fabricNode.json")


class TestCheckFabricHealth(unittest.TestCase):

    def _make_mock_client(self, nodes, faults, tenant, vrfs, bds, epgs):
        client = MagicMock(spec=APICClient)
        client.get.side_effect = [nodes, faults, tenant, vrfs, bds, epgs]
        return client

    def test_all_healthy(self):
        client = self._make_mock_client(
            MOCK_NODES_ALL_ACTIVE, MOCK_NO_FAULTS,
            MOCK_TENANT_EXISTS, MOCK_VRFS, MOCK_BDS, MOCK_EPGS
        )
        report = check_fabric_health(client, "ACME-PROD")
        self.assertTrue(report.healthy)
        self.assertEqual(report.node_count, 3)
        self.assertEqual(report.active_nodes, 3)
        self.assertEqual(report.critical_faults, 0)
        self.assertTrue(report.tenant_exists)
        self.assertEqual(report.vrf_count, 1)
        self.assertEqual(report.bd_count, 2)
        self.assertEqual(report.epg_count, 3)

    def test_inactive_node_makes_unhealthy(self):
        client = self._make_mock_client(
            MOCK_NODES_ONE_INACTIVE, MOCK_NO_FAULTS,
            MOCK_TENANT_EXISTS, MOCK_VRFS, MOCK_BDS, MOCK_EPGS
        )
        report = check_fabric_health(client, "ACME-PROD")
        self.assertFalse(report.healthy)
        self.assertEqual(report.active_nodes, 2)
        self.assertEqual(report.node_count, 3)
        self.assertIn("102", report.inactive_nodes[0])

    def test_critical_faults_make_unhealthy(self):
        client = self._make_mock_client(
            MOCK_NODES_ALL_ACTIVE, MOCK_CRITICAL_FAULTS,
            MOCK_TENANT_EXISTS, MOCK_VRFS, MOCK_BDS, MOCK_EPGS
        )
        report = check_fabric_health(client, "ACME-PROD")
        self.assertFalse(report.healthy)
        self.assertEqual(report.critical_faults, 2)
        self.assertIn("F0123", report.fault_codes)

    def test_tenant_not_found_makes_unhealthy(self):
        # When tenant doesn't exist, only 3 API calls are made (no VRF/BD/EPG queries)
        client = MagicMock(spec=APICClient)
        client.get.side_effect = [
            MOCK_NODES_ALL_ACTIVE,
            MOCK_NO_FAULTS,
            MOCK_TENANT_NOT_FOUND,
        ]
        report = check_fabric_health(client, "ACME-PROD")
        self.assertFalse(report.healthy)
        self.assertFalse(report.tenant_exists)
        self.assertEqual(report.vrf_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
