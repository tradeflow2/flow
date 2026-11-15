"""Stage 5 discount arithmetic. Rial amounts are integral Decimal values; floats are forbidden."""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR

ZERO = Decimal(0)
HUNDRED = Decimal(100)


def rial(value) -> Decimal:
    if isinstance(value, float):
        raise TypeError("binary float is not accepted for money")
    d = Decimal(str(value))
    if not d.is_finite() or d < 0:
        raise ValueError("money must be finite and non-negative")
    return d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def percent_discount(gross, percentage) -> Decimal:
    g, pct = rial(gross), Decimal(str(percentage))
    if not pct.is_finite() or pct < 0 or pct > HUNDRED:
        raise ValueError("percentage must be between 0 and 100")
    return (g * pct / HUNDRED).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def largest_remainder(total_discount, eligible_amounts):
    """Return allocations in input order. Each row: {line_no, eligible}; ties use line_no then id."""
    total = rial(total_discount)
    rows = []
    for index, item in enumerate(eligible_amounts):
        line_no = int(item["line_no"])
        eligible = rial(item["eligible"])
        rows.append({"index": index, "line_no": line_no, "id": str(item.get("id", "")), "eligible": eligible})
    capacity = sum((r["eligible"] for r in rows), ZERO)
    if total > capacity:
        raise ValueError("request discount exceeds eligible net amount")
    if total == 0:
        return [ZERO for _ in rows]
    if capacity == 0:
        raise ValueError("cannot allocate discount over zero eligible amount")
    shares = []
    used = ZERO
    for r in rows:
        exact = total * r["eligible"] / capacity
        floor = exact.to_integral_value(rounding=ROUND_FLOOR)
        shares.append({**r, "allocation": floor, "remainder": exact - floor})
        used += floor
    remaining = int(total - used)
    order = sorted(shares, key=lambda x: (-x["remainder"], x["line_no"], x["id"]))
    for row in order[:remaining]:
        row["allocation"] += 1
    result = [ZERO] * len(rows)
    for row in shares:
        if row["allocation"] > row["eligible"]:
            raise ValueError("allocation exceeds eligible amount")
        result[row["index"]] = row["allocation"]
    if sum(result, ZERO) != total:
        raise AssertionError("largest-remainder allocation does not reconcile")
    return result


def select_policy(product_policies, organization_policy, product_ids):
    """PRODUCT policy wins only for a single-product request; mixed requests use organization policy."""
    unique = set(map(str, product_ids))
    if len(unique) == 1:
        match = next((p for p in product_policies if str(p.get("scope_id")) in unique), None)
        if match is not None:
            return match
    return organization_policy


def approval_required(amount, gross, direct_limit_amount, direct_limit_percent):
    amount, gross = rial(amount), rial(gross)
    lim_amount, lim_pct = rial(direct_limit_amount), Decimal(str(direct_limit_percent))
    if amount == 0:
        return False
    if lim_pct < 0 or lim_pct > HUNDRED:
        raise ValueError("invalid direct percentage limit")
    return amount > lim_amount or (gross and amount * HUNDRED / gross > lim_pct)
