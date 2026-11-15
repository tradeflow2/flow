"""Invalidates active approvals in the same transaction as a commercial edit."""
from psycopg.types.json import Jsonb


def invalidate_approvals(conn, principal, request_id, reason):
    conn.execute("SELECT set_config('app.user_id',%s,true),set_config('app.organization_id',%s,true)",
                 (str(principal.user_id),str(principal.org_id)))
    conn.execute("SELECT current_version FROM request_commercial_state WHERE organization_id=%s AND request_id=%s FOR UPDATE",
                 (principal.org_id,request_id)).fetchone()
    conn.execute("SELECT id FROM commercial_snapshots WHERE organization_id=%s AND request_id=%s ORDER BY commercial_version FOR SHARE",
                 (principal.org_id,request_id)).fetchall()
    rows=conn.execute("SELECT id,status FROM discount_approvals WHERE organization_id=%s AND request_id=%s AND status IN ('DRAFT','APPROVED') ORDER BY id FOR UPDATE",
                      (principal.org_id,request_id)).fetchall()
    for row in rows:
        conn.execute("UPDATE discount_approvals SET status='INVALIDATED',invalidated_by=%s,invalidated_at=clock_timestamp() WHERE id=%s",
                     (principal.user_id,row['id']))
        conn.execute("""INSERT INTO discount_approval_history(organization_id,approval_id,request_id,event_type,actor_id,reason,event_data)
          VALUES(%s,%s,%s,'INVALIDATED',%s,%s,%s)""",
          (principal.org_id,row['id'],request_id,principal.user_id,reason,Jsonb({"invalidated_by":"commercial_edit"})))
