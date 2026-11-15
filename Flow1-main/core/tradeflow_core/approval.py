"""Pure approval lifecycle rules shared by API services and tests."""
from __future__ import annotations

STATUSES = {"DRAFT", "APPROVED", "REJECTED", "INVALIDATED"}


def assert_can_approve(*, role_code, actor_id, request_creator_id, discount_applied_by, requested_by, snapshot_creator_id=None,
                       request_status, approval_status, snapshot_current, snapshot_dirty, approval_required):
    if str(role_code).upper() != "ADMIN":
        raise PermissionError("ADMIN role required")
    if str(actor_id) in {str(x) for x in (request_creator_id, discount_applied_by, requested_by, snapshot_creator_id) if x is not None}:
        raise PermissionError("self approval is forbidden")
    if request_status not in {"DRAFT", "SUBMITTED"}:
        raise ValueError("request state does not allow approval")
    if approval_status != "DRAFT":
        raise ValueError("approval is not pending")
    if not snapshot_current or snapshot_dirty:
        raise ValueError("commercial snapshot is stale")
    if not approval_required:
        raise ValueError("approval is not required")


def assert_can_reject(*, role_code, approval_status, snapshot_current, snapshot_dirty, reason):
    if str(role_code).upper() != "ADMIN":
        raise PermissionError("ADMIN role required")
    if approval_status != "DRAFT" or not snapshot_current or snapshot_dirty:
        raise ValueError("approval is not rejectable")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise ValueError("a rejection reason of 1..500 characters is required")
    return reason.strip()
