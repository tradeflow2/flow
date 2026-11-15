import unittest
from decimal import Decimal as D
from tradeflow_core.discount import largest_remainder, percent_discount, approval_required, select_policy


class DiscountArithmetic(unittest.TestCase):
    def test_percent_round_half_up(self):
        self.assertEqual(percent_discount(D("10.5"), "10"), D(1))
        self.assertEqual(percent_discount(D("15"), "10"), D(2))

    def test_largest_remainder_exact_sum_and_deterministic_tie(self):
        rows=[{"line_no":2,"id":"b","eligible":D(1)}, {"line_no":1,"id":"a","eligible":D(1)}, {"line_no":3,"id":"c","eligible":D(1)}]
        out=largest_remainder(2,rows)
        self.assertEqual(out,[D(1),D(1),D(0)])
        self.assertEqual(sum(out),D(2))

    def test_largest_remainder_zero_and_capacity(self):
        self.assertEqual(largest_remainder(0,[{"line_no":1,"eligible":100}]),[D(0)])
        with self.assertRaises(ValueError): largest_remainder(2,[{"line_no":1,"eligible":1}])

    def test_approval_threshold_and_policy_precedence(self):
        self.assertTrue(approval_required(20,100,10,20))
        self.assertFalse(approval_required(0,100,0,0))
        org={"scope_type":"ORGANIZATION"}; prod={"scope_type":"PRODUCT","scope_id":"p1"}
        self.assertIs(select_policy([prod],org,["p1"]),prod)
        self.assertIs(select_policy([prod],org,["p1","p2"]),org)
