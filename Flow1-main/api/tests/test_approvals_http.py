"""Stage 5 maker-checker HTTP tests. Requires the Stage 5 migration and seeded demo users."""
import unittest
from tests.test_requests_http import RequestsBase, R
from tests import support


class ApprovalHttp(RequestsBase):
    def _policy(self):
        sql="""SELECT set_config('app.user_id',(SELECT id::text FROM users WHERE username='admin' AND organization_id=(SELECT id FROM organizations WHERE code='DEMO')),false);
        INSERT INTO discount_policies(organization_id,scope_type,scope_id,role_code,version,direct_limit_amount,
          direct_limit_percent,approver_limit_amount,approver_limit_percent,created_by)
          SELECT o.id,'ORGANIZATION',NULL,rs.code,1,0,0,99999999999999999999,100,u.id
          FROM organizations o JOIN users u ON u.organization_id=o.id AND u.username='admin'
          JOIN users s ON s.organization_id=o.id AND s.username IN ('sales','admin') JOIN roles rs ON rs.id=s.role_id
          WHERE o.code='DEMO' ON CONFLICT DO NOTHING"""
        rc,_,err=support.pg.psql(support.DB,sql)
        self.assertEqual(rc,0,err)

    def test_admin_approval_and_self_approval_block(self):
        self._policy()
        req=self.mk(headers=self.sales,items=[self.item()])
        self.assertEqual(self.c.put(f"{R}/{req['id']}/discount",json={"amount":"100"},headers=self.sales).status_code,200)
        self.assertEqual(self.c.post(f"{R}/{req['id']}/submit",headers=self.sales).status_code,200)
        create=self.c.post(f"{R}/{req['id']}/discount-approvals",headers=self.sales)
        self.assertEqual(create.status_code,201,create.text)
        aid=create.json()["data"]["id"]
        self_approve=self.c.post(f"/api/v1/discount-approvals/{aid}/approve",headers=self.sales)
        self.assertEqual(self_approve.status_code,403,self_approve.text)
        approve=self.c.post(f"/api/v1/discount-approvals/{aid}/approve",headers=self.admin)
        self.assertEqual(approve.status_code,200,approve.text)
        self.assertEqual(approve.json()["data"]["status"],"APPROVED")
        history=self.c.get(f"/api/v1/discount-approvals/{aid}/history",headers=self.admin)
        self.assertEqual(history.status_code,200,history.text)
        self.assertEqual([x["event_type"] for x in history.json()["data"]],["REQUESTED","APPROVED"])

    def test_reject_submitted_request_returns_it_to_draft(self):
        self._policy()
        req=self.mk(headers=self.sales,items=[self.item()])
        self.c.put(f"{R}/{req['id']}/discount",json={"amount":"100"},headers=self.sales)
        self.c.post(f"{R}/{req['id']}/submit",headers=self.sales)
        create=self.c.post(f"{R}/{req['id']}/discount-approvals",headers=self.sales)
        self.assertEqual(create.status_code,201,create.text)
        aid=create.json()["data"]["id"]
        reject=self.c.post(f"/api/v1/discount-approvals/{aid}/reject",json={"reason":"Needs revision"},headers=self.admin)
        self.assertEqual(reject.status_code,200,reject.text)
        self.assertEqual(support.scalar(f"SELECT status FROM purchase_requests WHERE id='{req['id']}'"),"DRAFT")

    def test_admin_who_created_and_applied_discount_cannot_self_approve(self):
        self._policy()
        req=self.mk(headers=self.admin,items=[self.item()])
        self.assertEqual(self.c.put(f"{R}/{req['id']}/discount",json={"amount":"100"},headers=self.admin).status_code,200)
        self.assertEqual(self.c.post(f"{R}/{req['id']}/submit",headers=self.admin).status_code,200)
        create=self.c.post(f"{R}/{req['id']}/discount-approvals",headers=self.admin)
        self.assertEqual(create.status_code,201,create.text)
        aid=create.json()["data"]["id"]
        result=self.c.post(f"/api/v1/discount-approvals/{aid}/approve",headers=self.admin)
        self.assertEqual(result.status_code,403,result.text)
