"""Tests for core/tradeflow_core/payment.py (Stage 6, S6-A)."""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from tradeflow_core import payment as pay
from tradeflow_core.errors import AppError, ErrorCode


TODAY = date(2026, 11, 15)


class ParseCreateBase(unittest.TestCase):
    def setUp(self):
        self.base = {
            "pre_invoice_id": "00000000-0000-0000-0000-000000000001",
            "payment_method": "CASH",
            "amount": "1000000",
            "payment_date": TODAY.isoformat(),
        }

    def _err(self, body, field, reason):
        with self.assertRaises(AppError) as ctx:
            pay.parse_create(body, today=TODAY)
        self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_ERROR)
        self.assertEqual(ctx.exception.details[0]["field"], field)
        self.assertEqual(ctx.exception.details[0]["reason"], reason)


class CashTests(ParseCreateBase):
    def test_valid_cash(self):
        out = pay.parse_create(self.base, today=TODAY)
        self.assertEqual(out["payment_method"], "CASH")
        self.assertEqual(out["amount"], Decimal(1000000))
        self.assertIsNone(out["cheque_number"])
        self.assertIsNone(out["cheque_status"])

    def test_cash_with_cheque_number_rejected(self):
        body = {**self.base, "cheque_number": "123"}
        self._err(body, "cheque_number", "NOT_ALLOWED_FOR_METHOD")

    def test_cash_with_bank_name_rejected(self):
        body = {**self.base, "bank_name": "Melli"}
        self._err(body, "bank_name", "NOT_ALLOWED_FOR_METHOD")


class BankTransferTests(ParseCreateBase):
    def setUp(self):
        super().setUp()
        self.base = {**self.base, "payment_method": "BANK_TRANSFER"}

    def test_valid_bank_transfer(self):
        body = {**self.base, "reference_number": "REF-1", "bank_name": "Melli"}
        out = pay.parse_create(body, today=TODAY)
        self.assertEqual(out["payment_method"], "BANK_TRANSFER")
        self.assertEqual(out["reference_number"], "REF-1")
        self.assertEqual(out["bank_name"], "Melli")

    def test_missing_reference_number_rejected(self):
        body = {**self.base, "bank_name": "Melli"}
        self._err(body, "reference_number", "REQUIRED")

    def test_missing_bank_name_rejected(self):
        body = {**self.base, "reference_number": "REF-1"}
        self._err(body, "bank_name", "REQUIRED")

    def test_bank_transfer_with_cheque_number_rejected(self):
        body = {**self.base, "reference_number": "REF-1", "bank_name": "Melli", "cheque_number": "123"}
        self._err(body, "cheque_number", "NOT_ALLOWED_FOR_METHOD")


class ChequeTests(ParseCreateBase):
    def setUp(self):
        super().setUp()
        self.base = {**self.base, "payment_method": "CHEQUE"}

    def test_valid_cheque(self):
        body = {
            **self.base,
            "cheque_number": "123456",
            "bank_name": "Melli",
            "cheque_due_date": (TODAY + timedelta(days=30)).isoformat(),
        }
        out = pay.parse_create(body, today=TODAY)
        self.assertEqual(out["payment_method"], "CHEQUE")
        self.assertEqual(out["cheque_number"], "123456")
        self.assertEqual(out["cheque_status"], "RECEIVED")

    def test_missing_cheque_number_rejected(self):
        body = {**self.base, "bank_name": "Melli", "cheque_due_date": (TODAY + timedelta(days=30)).isoformat()}
        self._err(body, "cheque_number", "REQUIRED")

    def test_missing_due_date_rejected(self):
        body = {**self.base, "cheque_number": "123456", "bank_name": "Melli"}
        self._err(body, "cheque_due_date", "REQUIRED")

    def test_due_date_before_payment_date_rejected(self):
        body = {
            **self.base,
            "cheque_number": "123456",
            "bank_name": "Melli",
            "cheque_due_date": (TODAY - timedelta(days=1)).isoformat(),
        }
        self._err(body, "cheque_due_date", "MUST_BE_AFTER_PAYMENT_DATE")


class CommonTests(ParseCreateBase):
    def test_invalid_method_rejected(self):
        body = {**self.base, "payment_method": "POS"}
        self._err(body, "payment_method", "UNKNOWN_PAYMENT_METHOD")

    def test_amount_zero_rejected(self):
        body = {**self.base, "amount": "0"}
        self._err(body, "amount", "MUST_BE_POSITIVE")

    def test_amount_negative_rejected(self):
        body = {**self.base, "amount": "-1000"}
        self._err(body, "amount", "MUST_BE_POSITIVE")

    def test_future_payment_date_rejected(self):
        body = {**self.base, "payment_date": (TODAY + timedelta(days=5)).isoformat()}
        self._err(body, "payment_date", "MUST_NOT_BE_IN_FUTURE")

    def test_missing_pre_invoice_id_rejected(self):
        body = {k: v for k, v in self.base.items() if k != "pre_invoice_id"}
        with self.assertRaises(AppError):
            pay.parse_create(body, today=TODAY)

    def test_unknown_field_rejected(self):
        body = {**self.base, "unknown_field": "x"}
        with self.assertRaises(AppError):
            pay.parse_create(body, today=TODAY)


class AllocationTests(unittest.TestCase):
    def test_exact_allocation_ok(self):
        pay.validate_allocation(
            pre_invoice_total=Decimal(1000),
            existing_confirmed=Decimal(500),
            existing_pending=Decimal(300),
            existing_credit=Decimal(0),
            new_amount=Decimal(200),
        )

    def test_over_allocation_rejected(self):
        with self.assertRaises(AppError) as ctx:
            pay.validate_allocation(
                pre_invoice_total=Decimal(1000),
                existing_confirmed=Decimal(500),
                existing_pending=Decimal(300),
                existing_credit=Decimal(0),
                new_amount=Decimal(300),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.STATE_CONFLICT)
        self.assertEqual(ctx.exception.details["reason"], "OVER_ALLOCATION")


if __name__ == "__main__":
    unittest.main()