"""HTTP tests for payments (Stage 6, S6-A)."""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from tests import support
from tests.test_requests_http import R, RequestsBase


API = "/api/v1"


def err(r):
    e = dict(r.json()["error"])
    e.pop("request_id")
    return e


@unittest.skipUnless(support.ready(), "SKIPPED: needs PostgreSQL (psql) and TF_SQL_DIR")
class PaymentsHttp(RequestsBase):
    # setUpClass is inherited from RequestsBase (database, client, headers, seed ids): one setup, no duplicates.

    def _payment_body(self, pre_invoice_id, **kw):
        return {
            "pre_invoice_id": pre_invoice_id,
            "payment_method": "CASH",
            "amount": "1000000",
            "payment_date": date.today().isoformat(),
            **kw,
        }

    def _new_pre_invoice(self):
        """An independent DRAFT Pre-Invoice (own request, total 1,000,000) so payments never leak between tests."""
        d = self.mk(headers=self.sales, items=[self.item(quantity="1000", unit_price="1000")])
        r = self.c.post(f"{R}/{d['id']}/submit", headers=self.sales)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.post(f"{R}/{d['id']}/pre-invoices", json={}, headers=self.sales)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id"]

    # ------------------------------------------------------------------ create
    def test_create_cash_payment(self):
        pid = self._new_pre_invoice()
        r = self.c.post(
            f"{API}/pre-invoices/{pid}/payments",
            json=self._payment_body(pid),
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()["data"]
        self.assertEqual(d["payment_method"], "CASH")
        self.assertEqual(d["status"], "PENDING_CONFIRMATION")
        self.assertEqual(d["amount"], "1000000")

    def test_create_bank_transfer(self):
        pid = self._new_pre_invoice()
        body = self._payment_body(
            pid,
            payment_method="BANK_TRANSFER",
            reference_number="REF-1",
            bank_name="Melli",
        )
        r = self.c.post(
            f"{API}/pre-invoices/{pid}/payments",
            json=body,
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()["data"]
        self.assertEqual(d["payment_method"], "BANK_TRANSFER")
        self.assertEqual(d["reference_number"], "REF-1")
        self.assertEqual(d["bank_name"], "Melli")

    def test_create_cheque(self):
        pid = self._new_pre_invoice()
        body = self._payment_body(
            pid,
            payment_method="CHEQUE",
            cheque_number="123456",
            bank_name="Melli",
            cheque_due_date=(date.today() + timedelta(days=30)).isoformat(),
        )
        r = self.c.post(
            f"{API}/pre-invoices/{pid}/payments",
            json=body,
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 201, r.text)
        d = r.json()["data"]
        self.assertEqual(d["payment_method"], "CHEQUE")
        self.assertEqual(d["cheque_number"], "123456")
        self.assertEqual(d["cheque_status"], "RECEIVED")

    def test_create_invalid_method(self):
        pid = self._new_pre_invoice()
        r = self.c.post(
            f"{API}/pre-invoices/{pid}/payments",
            json=self._payment_body(pid, payment_method="POS"),
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 422)

    def test_create_cash_with_cheque_fields(self):
        pid = self._new_pre_invoice()
        r = self.c.post(
            f"{API}/pre-invoices/{pid}/payments",
            json=self._payment_body(pid, cheque_number="123"),
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 422)
        self.assertEqual(err(r)["code"], "VALIDATION_ERROR")

    def test_create_over_allocation(self):
        pid = self._new_pre_invoice()
        # First payment of 1M
        r1 = self.c.post(
            f"{API}/pre-invoices/{pid}/payments",
            json=self._payment_body(pid),
            headers=self.admin,
        )
        self.assertEqual(r1.status_code, 201, r1.text)
        # Second payment of 999M (should exceed total)
        r = self.c.post(
            f"{API}/pre-invoices/{pid}/payments",
            json=self._payment_body(pid, amount="999000000"),
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(err(r)["code"], "STATE_CONFLICT")
        self.assertEqual(err(r)["details"]["reason"], "OVER_ALLOCATION")

    # ------------------------------------------------------------------ list
    def test_list_payments(self):
        pid = self._new_pre_invoice()
        r0 = self.c.post(
            f"{API}/pre-invoices/{pid}/payments",
            json=self._payment_body(pid),
            headers=self.admin,
        )
        self.assertEqual(r0.status_code, 201, r0.text)
        r = self.c.get(
            f"{API}/pre-invoices/{pid}/payments",
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()["items"]
        self.assertGreaterEqual(len(items), 1)

    # ------------------------------------------------------------------ get
    def test_get_payment(self):
        pi_id = self._new_pre_invoice()
        r = self.c.post(
            f"{API}/pre-invoices/{pi_id}/payments",
            json=self._payment_body(pi_id),
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 201, r.text)
        pid = r.json()["data"]["id"]
        r2 = self.c.get(f"{API}/payments/{pid}", headers=self.admin)
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["data"]["id"], pid)

    # ------------------------------------------------------------------ tenant isolation
    def test_tenant_isolation(self):
        pid = self._new_pre_invoice()
        r = self.c.get(
            f"{API}/pre-invoices/{pid}/payments",
            headers=self.other,
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()