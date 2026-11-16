"""HTTP tests for payments (Stage 6, S6-A)."""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests import support


API = "/api/v1"


def err(r):
    e = dict(r.json()["error"])
    e.pop("request_id")
    return e


@unittest.skipUnless(support.ready(), "SKIPPED: needs PostgreSQL (psql) and TF_SQL_DIR")
class PaymentsHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.build_db()
        cls.c = TestClient(support.make_app())
        cls.admin = support.headers("admin")
        cls.sales = support.headers("sales")
        cls.acct = support.headers("accounting")
        cls.other = support.headers("admin", "OTHER")

        # Create a DRAFT Pre-Invoice for testing
        cls.pre_invoice_id = support.scalar(
            """
            SELECT pi.id
              FROM pre_invoices pi
              JOIN purchase_requests r
                ON r.id = pi.source_request_id
               AND r.organization_id = pi.organization_id
             WHERE pi.organization_id = (SELECT id FROM organizations WHERE code = 'DEMO')
               AND pi.status = 'DRAFT'
             LIMIT 1
            """
        )

    def _payment_body(self, **kw):
        return {
            "pre_invoice_id": self.pre_invoice_id,
            "payment_method": "CASH",
            "amount": "1000000",
            "payment_date": date.today().isoformat(),
            **kw,
        }

    # ------------------------------------------------------------------ create
    def test_create_cash_payment(self):
        r = self.c.post(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            json=self._payment_body(),
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()["data"]
        self.assertEqual(d["payment_method"], "CASH")
        self.assertEqual(d["status"], "PENDING_CONFIRMATION")
        self.assertEqual(d["amount"], "1000000")

    def test_create_bank_transfer(self):
        body = self._payment_body(
            payment_method="BANK_TRANSFER",
            reference_number="REF-1",
            bank_name="Melli",
        )
        r = self.c.post(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            json=body,
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()["data"]
        self.assertEqual(d["payment_method"], "BANK_TRANSFER")
        self.assertEqual(d["reference_number"], "REF-1")
        self.assertEqual(d["bank_name"], "Melli")

    def test_create_cheque(self):
        body = self._payment_body(
            payment_method="CHEQUE",
            cheque_number="123456",
            bank_name="Melli",
            cheque_due_date=(date.today() + timedelta(days=30)).isoformat(),
        )
        r = self.c.post(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            json=body,
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()["data"]
        self.assertEqual(d["payment_method"], "CHEQUE")
        self.assertEqual(d["cheque_number"], "123456")
        self.assertEqual(d["cheque_status"], "RECEIVED")

    def test_create_invalid_method(self):
        r = self.c.post(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            json=self._payment_body(payment_method="POS"),
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 422)

    def test_create_cash_with_cheque_fields(self):
        r = self.c.post(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            json=self._payment_body(cheque_number="123"),
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 422)
        self.assertEqual(err(r)["code"], "VALIDATION_ERROR")

    def test_create_over_allocation(self):
        # First payment of 1M
        self.c.post(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            json=self._payment_body(),
            headers=self.admin,
        )
        # Second payment of 999M (should exceed total)
        r = self.c.post(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            json=self._payment_body(amount="999000000"),
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(err(r)["code"], "STATE_CONFLICT")
        self.assertEqual(err(r)["details"]["reason"], "OVER_ALLOCATION")

    # ------------------------------------------------------------------ list
    def test_list_payments(self):
        self.c.post(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            json=self._payment_body(),
            headers=self.admin,
        )
        r = self.c.get(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()["items"]
        self.assertGreaterEqual(len(items), 1)

    # ------------------------------------------------------------------ get
    def test_get_payment(self):
        r = self.c.post(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            json=self._payment_body(),
            headers=self.admin,
        )
        pid = r.json()["data"]["id"]
        r2 = self.c.get(f"{API}/payments/{pid}", headers=self.admin)
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["data"]["id"], pid)

    # ------------------------------------------------------------------ tenant isolation
    def test_tenant_isolation(self):
        r = self.c.get(
            f"{API}/pre-invoices/{self.pre_invoice_id}/payments",
            headers=self.other,
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()