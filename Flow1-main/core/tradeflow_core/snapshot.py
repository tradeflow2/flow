"""Build immutable Commercial Snapshot payloads from locked, organization-scoped rows."""
from __future__ import annotations
from decimal import Decimal
from .discount import largest_remainder, rial


def build_snapshot(*, request, customer, items, policy, request_discount, created_by, commercial_version):
    if not items:
        raise ValueError("a commercial snapshot requires at least one item")
    ordered = sorted(items, key=lambda x: (x["created_at"], str(x["id"])))
    item_discounts = []
    eligible = []
    subtotal = Decimal(0)
    item_discount_total = Decimal(0)
    for n, row in enumerate(ordered, 1):
        gross = rial(Decimal(row["quantity"]) * Decimal(row["unit_price"]))
        disc = rial(row["discount_amount"])
        if disc > gross:
            raise ValueError("item discount exceeds gross amount")
        subtotal += gross
        item_discount_total += disc
        item_discounts.append({"line_no": n, "row": row, "gross": gross, "discount": disc})
        eligible.append({"line_no": n, "id": str(row["id"]), "eligible": gross-disc})
    request_discount = rial(request_discount)
    allocations = largest_remainder(request_discount, eligible)
    snapshot_items = []
    tax_total = Decimal(0)
    for entry, allocation in zip(item_discounts, allocations):
        row, gross, item_discount = entry["row"], entry["gross"], entry["discount"]
        net = gross-item_discount-allocation
        # Tax is an externally supplied item amount; Stage 5 does not invent a tax rate.
        tax = rial(row.get("tax_amount", 0))
        tax_total += tax
        snapshot_items.append({
            "line_no": entry["line_no"], "source_item_id": str(row["id"]),
            "product_id": str(row["product_id"]), "product_code": row["product_code"],
            "product_name": row["product_name"], "unit_id": str(row["unit_id"]),
            "unit_code": row["unit_code"], "unit_name": row["unit_name"],
            "quantity": row["quantity"], "bundle_count": row["bundle_count"],
            "estimated_weight": row["estimated_weight"], "unit_price": rial(row["unit_price"]),
            "gross_amount": gross, "item_discount_amount": item_discount, "discount_applied_by": str(row["discount_applied_by"]) if row.get("discount_applied_by") else None,
            "request_discount_allocation": allocation, "net_amount": net,
            "tax_amount": tax, "line_total": net+tax, "notes": row.get("notes"),
            "specifications": row.get("specifications") or [], "policy_snapshot": row.get("policy_snapshot", policy or {}),
        })
    discount_total = item_discount_total + request_discount
    grand_total = subtotal-discount_total+tax_total
    gross_base = subtotal
    policy_data = policy or {}
    required = bool(policy_data.get("approval_required", False))
    return {
        "header": {
            "request_id": str(request["id"]), "request_number": request["request_number"],
            "request_date": request["request_date"], "requested_delivery_date": request["requested_delivery_date"],
            "customer_id": str(customer["id"]),
            "customer_snapshot": {k: customer.get(k) for k in ("customer_type","name","national_id","economic_code","registration_number","mobile","phone","email","address","postal_code")},
            "subtotal": subtotal, "item_discount_total": item_discount_total,
            "request_discount_amount": request_discount, "request_discount_applied_by": str(request["discount_applied_by"]) if request.get("discount_applied_by") else None, "discount_total": discount_total,
            "tax_total": tax_total, "grand_total": grand_total,
            "approval_required": required, "policy_snapshot": policy_data,
            "created_by": str(created_by), "commercial_version": int(commercial_version),
        },
        "items": snapshot_items,
    }
