"""Payment registration and validation for Stage 6 (S6-A).

Pure logic: no database, no HTTP. Validates payment method,
method-specific fields, amount, and payment date.

Related: ADR-065 (S6-A), ADR-058 (money), ADR-012 (payment != release).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from . import inputs
from .errors import AppError, ErrorCode
from .money import parse_money


PAYMENT_METHODS = ("CASH", "BANK_TRANSFER", "CHEQUE")
PAYMENT_STATUSES = ("PENDING_CONFIRMATION", "CONFIRMED", "REJECTED", "REVERSED")
CHEQUE_STATUSES = ("RECEIVED", "DEPOSITED", "CLEARED", "BOUNCED")

# Required and forbidden fields per method.
_METHOD_FIELDS = {
    "CASH": {
        "required": (),
        "forbidden": ("cheque_number", "cheque_due_date", "cheque_status",
                      "reference_number", "bank_name"),
    },
    "BANK_TRANSFER": {
        "required": ("reference_number", "bank_name"),
        "forbidden": ("cheque_number", "cheque_due_date", "cheque_status"),
    },
    "CHEQUE": {
        "required": ("cheque_number", "bank_name", "cheque_due_date"),
        "forbidden": ("reference_number",),
    },
}

# PAYMENT Audit payload is intentionally narrower than the API serializer.
# Keep this closed allowlist aligned with ADR-065 and STAGE_06_CONTRACT.
PAYMENT_AUDIT_FIELDS = (
    "id", "pre_invoice_id", "pre_invoice_number", "payment_method",
    "amount", "payment_date", "status", "cheque_status", "cheque_due_date",
)


def build_create_audit_payload(payment_data: dict) -> dict:
    """Return the exact allowlisted payload for a PAYMENT CREATE audit event."""
    return {field: payment_data[field] for field in PAYMENT_AUDIT_FIELDS}


PAYMENT_FIELDS = (
    "pre_invoice_id",
    "payment_method",
    "amount",
    "payment_date",
    "reference_number",
    "payer_name",
    "bank_name",
    "cheque_number",
    "cheque_due_date",
    "cheque_status",
    "notes",
)


def _parse_date(value, field: str) -> date:
    """Parse a YYYY-MM-DD date string."""
    if not isinstance(value, str):
        raise _verr(field, "MUST_BE_STRING")
    s = value.strip()
    if not s:
        raise _verr(field, "REQUIRED")
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise _verr(field, "INVALID_DATE")


def parse_create(body: dict, today: date | None = None) -> dict:
    """Validate and normalize a payment creation payload.

    Raises AppError with ErrorCode.VALIDATION_ERROR on invalid input.
    """
    inputs.strict_object(body, PAYMENT_FIELDS,
                         required=("pre_invoice_id", "payment_method",
                                   "amount", "payment_date"))

    today = today or date.today()

    method = body["payment_method"]
    if method not in PAYMENT_METHODS:
        raise _verr("payment_method", "UNKNOWN_PAYMENT_METHOD")
    try:
        amount = parse_money(body["amount"], "amount")
    except AppError:
        raise _verr("amount", "MUST_BE_POSITIVE")
    if amount <= 0:
        raise _verr("amount", "MUST_BE_POSITIVE")

    payment_date = _parse_date(body["payment_date"], "payment_date")
    if payment_date > today + timedelta(days=1):
        raise _verr("payment_date", "MUST_NOT_BE_IN_FUTURE")

    out: dict = {
        "pre_invoice_id": inputs.parse_uuid(body["pre_invoice_id"], "pre_invoice_id"),
        "payment_method": method,
        "amount": amount,
        "payment_date": payment_date,
        "reference_number": None,
        "payer_name": None,
        "bank_name": None,
        "cheque_number": None,
        "cheque_due_date": None,
        "cheque_status": None,
        "notes": inputs.text(body.get("notes"), "notes", inputs.TEXT_LIMIT),
    }

    spec = _METHOD_FIELDS[method]

    # Required fields
    for field in spec["required"]:
        if body.get(field) is None:
            raise _verr(field, "REQUIRED")
        if field == "cheque_due_date":
            out[field] = _parse_date(body[field], field)
            if out[field] < payment_date:
                raise _verr(field, "MUST_BE_AFTER_PAYMENT_DATE")
        elif field == "cheque_number":
            out[field] = inputs.text(body[field], field, 50)
        elif field == "reference_number":
            out[field] = inputs.text(body[field], field, 100)
        elif field == "bank_name":
            out[field] = inputs.text(body[field], field, 100)

    # Forbidden fields
    for field in spec["forbidden"]:
        if body.get(field) is not None:
            raise _verr(field, "NOT_ALLOWED_FOR_METHOD")

    # Optional fields
    if "payer_name" in body and body["payer_name"] is not None:
        out["payer_name"] = inputs.text(body["payer_name"], "payer_name", 255)

    # For CHEQUE, initial status is RECEIVED
    if method == "CHEQUE":
        out["cheque_status"] = "RECEIVED"

    return out


def parse_patch(body: dict, current: dict) -> dict:
    """Validate a patch payload (only allowed while PENDING_CONFIRMATION)."""
    if current["status"] != "PENDING_CONFIRMATION":
        raise AppError(
            ErrorCode.STATE_CONFLICT,
            details={"reason": "PAYMENT_NOT_PENDING", "status": current["status"]},
        )

    inputs.strict_object(body, PAYMENT_FIELDS, required=())

    out: dict = {}
    for field in PAYMENT_FIELDS:
        if field not in body:
            continue
        value = body[field]
        if field == "payment_method" and value is not None:
            if value not in PAYMENT_METHODS:
                raise _verr(field, "UNKNOWN_PAYMENT_METHOD")
            out[field] = value
        elif field == "amount" and value is not None:
            amt = parse_money(value, field)
            if amt <= 0:
                raise _verr(field, "MUST_BE_POSITIVE")
            out[field] = amt
        elif field == "payment_date" and value is not None:
            out[field] = _parse_date(value, field)
        elif field == "pre_invoice_id":
            raise _verr(field, "IMMUTABLE")
        elif field == "notes":
            out[field] = inputs.text(value, field, inputs.TEXT_LIMIT)
        elif field in ("reference_number", "payer_name", "bank_name",
                       "cheque_number"):
            if value is not None:
                out[field] = inputs.text(value, field,
                                         100 if field != "payer_name" else 255)
            else:
                out[field] = None
        elif field == "cheque_due_date":
            out[field] = None if value is None else _parse_date(value, field)
        elif field == "cheque_status":
            if value is not None and value not in CHEQUE_STATUSES:
                raise _verr(field, "UNKNOWN_CHEQUE_STATUS")
            out[field] = value

    return out


def validate_allocation(
    pre_invoice_total: Decimal,
    existing_confirmed: Decimal,
    existing_pending: Decimal,
    existing_credit: Decimal,
    new_amount: Decimal,
) -> None:
    """Ensure the new payment does not exceed the Pre-Invoice total.

    All amounts are whole Rial (Decimal). Raises STATE_CONFLICT if the
    allocation would exceed the Pre-Invoice grand_total.
    """
    allocated = existing_confirmed + existing_pending + existing_credit + new_amount
    if allocated > pre_invoice_total:
        raise AppError(
            ErrorCode.STATE_CONFLICT,
            details={
                "reason": "OVER_ALLOCATION",
                "pre_invoice_total": str(pre_invoice_total),
                "allocated": str(allocated),
                "new_amount": str(new_amount),
            },
        )


def _verr(field: str, reason: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR,
        details=[{"field": field, "reason": reason}],
    )