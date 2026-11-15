import os
import unittest
from . import pg


@unittest.skipUnless(pg.available() and os.environ.get("TF_SQL_DIR"),"real PostgreSQL + TF_SQL_DIR required")
class Stage5Migration(unittest.TestCase):
    def test_stage5_installs_eight_tables_and_integrity_triggers(self):
        db="tf_stage5_schema_test"
        pg.make_db(db,os.environ["TF_SQL_DIR"])
        rc,out,err=pg.psql(db,"""SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN
          ('discount_policies','request_commercial_state','commercial_snapshots','commercial_snapshot_items','discount_approvals',
           'discount_approval_history','pre_invoice_commercial_links','discount_application_events');
          SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN
          ('trg_stage5_request_state_create','trg_stage5_request_dirty','trg_stage5_item_dirty','trg_pre_invoices_snapshot_gate');""")
        self.assertEqual(rc,0,err)
        self.assertEqual(out.splitlines()[-2:],["8","4"])
