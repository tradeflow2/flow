"""Payment-specific Audit payload tests for Stage 6 (S6-A)."""
from __future__ import annotations

import unittest

from tradeflow_core import audit, payment as pay


class PaymentCreateAuditPayloadTests(unittest.TestCase):
    def setUp(self):
        self.payment_data = {
            "id": "payment-id",
            "organization_id": "tenant-secret-id",
            "pre_invoice_id": "pre-invoice-id",
            "pre_invoice_number": "PI-1405-001",
            "payment_method": "BANK_TRANSFER",
            "amount": "1250000",
            "payment_date": "2026-11-15",
            "reference_number": "private-reference",
            "payer_name": "private-payer",
            "bank_name": "private-bank",
            "cheque_number": "private-cheque",
            "cheque_due_date": None,
            "cheque_status": None,
            "status": "PENDING_CONFIRMATION",
            "notes": "private-note",
            "created_by": "creator-id",
            "confirmed_by": "confirmer-id",
            "reversed_by": "reverser-id",
            "created_at": "2026-11-15T10:00:00+00:00",
            "confirmed_at": None,
            "reversed_at": None,
            "reversal_reason": "private-reason",
        }

    def test_create_payload_has_exact_nine_field_allowlist(self):
        payload = pay.build_create_audit_payload(self.payment_data)
        self.assertEqual(set(payload), set(pay.PAYMENT_AUDIT_FIELDS))
        self.assertEqual(
            set(payload),
            {
                "id", "pre_invoice_id", "pre_invoice_number",
                "payment_method", "amount", "payment_date", "status",
                "cheque_status", "cheque_due_date",
            },
        )

    def test_create_payload_excludes_all_non_allowlisted_values(self):
        payload = pay.build_create_audit_payload(self.payment_data)
        serialized = repr(payload)
        for value in (
            "tenant-secret-id", "private-reference", "private-payer",
            "private-bank", "private-cheque", "private-note",
            "creator-id", "confirmer-id", "reverser-id",
            "private-reason",
        ):
            self.assertNotIn(value, serialized)

    def test_missing_allowlisted_field_is_a_programming_error(self):
        incomplete = dict(self.payment_data)
        del incomplete["pre_invoice_number"]
        with self.assertRaises(KeyError):
            pay.build_create_audit_payload(incomplete)

    def test_payment_entity_is_accepted_by_audit_vocabulary(self):
        payload = pay.build_create_audit_payload(self.payment_data)
        entry = audit.build_entry(
            "CREATE",
            "PAYMENT",
            organization_id="tenant-id",
            user_id="user-id",
            entity_id=self.payment_data["id"],
            new_data=payload,
        )
        self.assertEqual(entry["entity_type"], "PAYMENT")
        self.assertEqual(entry["action"], "CREATE")
        self.assertEqual(entry["new_data"], payload)


if __name__ == "__main__":
    unittest.main()
