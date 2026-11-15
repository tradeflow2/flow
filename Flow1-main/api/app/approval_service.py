"""Maker-checker approval service. Each decision and its history is one database transaction."""
from psycopg.types.json import Jsonb
from tradeflow_core.approval import assert_can_approve, assert_can_reject
from tradeflow_core.errors import AppError, ErrorCode
from tradeflow_core.authz import Cap, authorize
from tradeflow_core import inputs
from .snapshot_service import SnapshotService

class ApprovalService:
    def __init__(self,db): self.db=db; self.snapshots=SnapshotService(db)
    def request(self,p,request_id):
        request_id=inputs.parse_uuid(request_id,"id")
        def work(c):
            owner=c.execute("SELECT created_by FROM purchase_requests WHERE id=%s AND organization_id=%s FOR UPDATE",(request_id,p.org_id)).fetchone()
            if not owner: raise AppError(ErrorCode.NOT_FOUND)
            authorize(p.role_code,Cap.REQUEST_WRITE,caller_id=p.user_id,owner_id=str(owner["created_by"]))
            snap=self.snapshots.create_in_tx(c,p,request_id)
            if not snap["approval_required"]: raise AppError(ErrorCode.STATE_CONFLICT,details={"reason":"APPROVAL_NOT_REQUIRED"})
            existing=c.execute("SELECT * FROM discount_approvals WHERE organization_id=%s AND snapshot_id=%s FOR UPDATE",(p.org_id,snap["id"])).fetchone()
            if existing:
                if existing["status"] in {"REJECTED","INVALIDATED"}:
                    raise AppError(ErrorCode.STATE_CONFLICT,details={"reason":"SNAPSHOT_ALREADY_DECIDED"})
                return {"id":str(existing["id"]),"request_id":str(existing["request_id"]),"snapshot_id":str(existing["snapshot_id"]),
                        "commercial_version":existing["commercial_version"],"status":existing["status"],"requested_by":str(existing["requested_by"])}
            aid=c.execute("""INSERT INTO discount_approvals(organization_id,request_id,snapshot_id,commercial_version,status,requested_by)
              VALUES(%s,%s,%s,%s,'DRAFT',%s) RETURNING *""",(p.org_id,request_id,snap["id"],snap["commercial_version"],p.user_id)).fetchone()
            c.execute("INSERT INTO discount_approval_history(organization_id,approval_id,request_id,event_type,actor_id,event_data) VALUES(%s,%s,%s,'REQUESTED',%s,%s)",
                      (p.org_id,aid["id"],request_id,p.user_id,Jsonb({"snapshot_id":str(snap["id"]),"commercial_version":snap["commercial_version"]})))
            return {"id":str(aid["id"]),"request_id":str(aid["request_id"]),"snapshot_id":str(aid["snapshot_id"]),
                    "commercial_version":aid["commercial_version"],"status":aid["status"],"requested_by":str(aid["requested_by"])}
        return self.db.run(work)
    def _load_locked(self,c,p,approval_id):
        c.execute("SELECT set_config('app.user_id',%s,true),set_config('app.organization_id',%s,true)",(str(p.user_id),str(p.org_id)))
        hint=c.execute("SELECT request_id,snapshot_id FROM discount_approvals WHERE id=%s AND organization_id=%s",(approval_id,p.org_id)).fetchone()
        if not hint: raise AppError(ErrorCode.NOT_FOUND)
        r=c.execute("SELECT * FROM purchase_requests WHERE id=%s AND organization_id=%s FOR UPDATE",(hint["request_id"],p.org_id)).fetchone()
        if not r: raise AppError(ErrorCode.NOT_FOUND)
        st=c.execute("SELECT * FROM request_commercial_state WHERE request_id=%s AND organization_id=%s FOR UPDATE",(hint["request_id"],p.org_id)).fetchone()
        s=c.execute("SELECT * FROM commercial_snapshots WHERE id=%s AND organization_id=%s FOR SHARE",(hint["snapshot_id"],p.org_id)).fetchone()
        a=c.execute("SELECT * FROM discount_approvals WHERE id=%s AND organization_id=%s FOR UPDATE",(approval_id,p.org_id)).fetchone()
        return a,r,st,s
    def approve(self,p,approval_id,ip=None):
        approval_id=inputs.parse_uuid(approval_id,"approval_id")
        def work(c):
            a,r,st,s=self._load_locked(c,p,approval_id)
            item_makers={str(x["discount_applied_by"]) for x in c.execute("SELECT DISTINCT discount_applied_by FROM purchase_request_items WHERE organization_id=%s AND purchase_request_id=%s AND discount_applied_by IS NOT NULL",(p.org_id,a["request_id"])).fetchall()}
            if str(p.user_id) in item_makers: raise AppError(ErrorCode.FORBIDDEN_ROLE,details={"reason":"SELF_APPROVAL_FORBIDDEN"})
            try: assert_can_approve(role_code=p.role_code,actor_id=p.user_id,request_creator_id=r["created_by"],
                discount_applied_by=r["discount_applied_by"],requested_by=a["requested_by"],snapshot_creator_id=s["created_by"],request_status=r["status"],
                approval_status=a["status"],snapshot_current=bool(st and st["current_version"]==a["commercial_version"]),
                snapshot_dirty=not bool(st and not st["is_dirty"]),approval_required=bool(s["approval_required"]))
            except PermissionError: raise AppError(ErrorCode.FORBIDDEN_ROLE)
            except ValueError as e: raise AppError(ErrorCode.STATE_CONFLICT,details={"reason":str(e)})
            c.execute("UPDATE discount_approvals SET status='APPROVED',decided_by=%s,decided_at=clock_timestamp() WHERE id=%s",
                      (p.user_id,approval_id))
            c.execute("INSERT INTO discount_approval_history(organization_id,approval_id,request_id,event_type,actor_id,event_data) VALUES(%s,%s,%s,'APPROVED',%s,%s)",
                      (p.org_id,approval_id,a["request_id"],p.user_id,Jsonb({"ip":ip})))
            return {"id":str(approval_id),"status":"APPROVED"}
        return self.db.run(work)
    def reject(self,p,approval_id,reason,ip=None):
        approval_id=inputs.parse_uuid(approval_id,"approval_id")
        def work(c):
            a,r,st,s=self._load_locked(c,p,approval_id)
            try: clean=assert_can_reject(role_code=p.role_code,approval_status=a["status"],
                snapshot_current=bool(st and st["current_version"]==a["commercial_version"]),
                snapshot_dirty=not bool(st and not st["is_dirty"]),reason=reason)
            except PermissionError: raise AppError(ErrorCode.FORBIDDEN_ROLE)
            except ValueError as e: raise AppError(ErrorCode.STATE_CONFLICT,details={"reason":str(e)})
            c.execute("UPDATE discount_approvals SET status='REJECTED',decided_by=%s,decided_at=clock_timestamp(),decision_reason=%s,rejected_txid=txid_current() WHERE id=%s",
                      (p.user_id,clean,approval_id))
            c.execute("INSERT INTO discount_approval_history(organization_id,approval_id,request_id,event_type,actor_id,reason,event_data) VALUES(%s,%s,%s,'REJECTED',%s,%s,%s)",
                      (p.org_id,approval_id,a["request_id"],p.user_id,clean,Jsonb({"ip":ip})))
            if r["status"]=="SUBMITTED":
                c.execute("UPDATE purchase_requests SET status='DRAFT',updated_by=%s,updated_at=clock_timestamp() WHERE id=%s AND organization_id=%s",
                          (p.user_id,a["request_id"],p.org_id))
            return {"id":str(approval_id),"status":"REJECTED","request_status":"DRAFT" if r["status"] in {"DRAFT","SUBMITTED"} else r["status"]}
        return self.db.run(work)
    def list_for_request(self,p,request_id):
        request_id=inputs.parse_uuid(request_id,"id")
        def work(c):
            owner=c.execute("SELECT created_by FROM purchase_requests WHERE id=%s AND organization_id=%s",(request_id,p.org_id)).fetchone()
            if not owner: raise AppError(ErrorCode.NOT_FOUND)
            authorize(p.role_code,Cap.REQUEST_READ,caller_id=p.user_id,owner_id=str(owner["created_by"]))
            return [{"id":str(x["id"]),"request_id":str(x["request_id"]),"snapshot_id":str(x["snapshot_id"]),
                     "commercial_version":x["commercial_version"],"status":x["status"],"requested_by":str(x["requested_by"]),
                     "decided_by":str(x["decided_by"]) if x["decided_by"] else None,"requested_at":x["requested_at"],
                     "decided_at":x["decided_at"],"decision_reason":x["decision_reason"],
                     "invalidated_by":str(x["invalidated_by"]) if x["invalidated_by"] else None,"invalidated_at":x["invalidated_at"]}
                    for x in c.execute("SELECT * FROM discount_approvals WHERE organization_id=%s AND request_id=%s ORDER BY requested_at DESC,id DESC",(p.org_id,request_id)).fetchall()]
        return self.db.run(work)

    def history(self,p,approval_id):
        approval_id=inputs.parse_uuid(approval_id,"approval_id")
        def work(c):
            row=c.execute("SELECT request_id FROM discount_approvals WHERE id=%s AND organization_id=%s",(approval_id,p.org_id)).fetchone()
            if not row: raise AppError(ErrorCode.NOT_FOUND)
            owner=c.execute("SELECT created_by FROM purchase_requests WHERE id=%s AND organization_id=%s",(row["request_id"],p.org_id)).fetchone()
            if not owner: raise AppError(ErrorCode.NOT_FOUND)
            authorize(p.role_code,Cap.REQUEST_READ,caller_id=p.user_id,owner_id=str(owner["created_by"]))
            return [{"id":x["id"],"event_type":x["event_type"],"actor_id":str(x["actor_id"]),"reason":x["reason"],
                     "event_data":x["event_data"],"created_at":x["created_at"]}
                    for x in c.execute("SELECT * FROM discount_approval_history WHERE organization_id=%s AND approval_id=%s ORDER BY id",
                                        (p.org_id,approval_id)).fetchall()]
        return self.db.run(work)
