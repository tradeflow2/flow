import unittest
from tradeflow_core.approval import assert_can_approve, assert_can_reject
from tradeflow_core.errors import map_pg_error, ErrorCode


class ApprovalRules(unittest.TestCase):
    def test_admin_cannot_self_approve(self):
        with self.assertRaises(PermissionError):
            assert_can_approve(role_code="ADMIN",actor_id="u1",request_creator_id="u1",discount_applied_by="u2",
                requested_by="u3",request_status="DRAFT",approval_status="DRAFT",snapshot_current=True,snapshot_dirty=False,approval_required=True)

    def test_approval_valid_current_snapshot(self):
        assert_can_approve(role_code="ADMIN",actor_id="admin",request_creator_id="creator",discount_applied_by="seller",
            requested_by="requester",request_status="SUBMITTED",approval_status="DRAFT",snapshot_current=True,snapshot_dirty=False,approval_required=True)

    def test_sqlstate_42501_maps_to_forbidden(self):
        self.assertEqual(map_pg_error("42501").code,ErrorCode.FORBIDDEN_ROLE)

    def test_reject_requires_reason_and_draft(self):
        self.assertEqual(assert_can_reject(role_code="ADMIN",approval_status="DRAFT",snapshot_current=True,snapshot_dirty=False,reason="Not justified"),"Not justified")
        with self.assertRaises(ValueError): assert_can_reject(role_code="ADMIN",approval_status="DRAFT",snapshot_current=True,snapshot_dirty=False,reason=" ")
