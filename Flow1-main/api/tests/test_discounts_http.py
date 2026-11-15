"""Stage 5 discount HTTP contract. Uses the real PostgreSQL fixtures; not executed in this package build."""
import unittest
from fastapi.testclient import TestClient
from tests import support
from tests.test_requests_http import RequestsBase, R


class DiscountHttp(RequestsBase):
    def test_set_and_read_request_discount(self):
        req=self.mk(headers=self.sales,items=[self.item()])
        url=f"{R}/{req['id']}/discount"
        put=self.c.put(url,json={"amount":"125"},headers=self.sales)
        self.assertEqual(put.status_code,200,put.text)
        get=self.c.get(url,headers=self.sales)
        self.assertEqual(get.status_code,200,get.text)
        self.assertEqual(get.json()["data"]["request_discount_amount"],"125")

    def test_discount_cannot_exceed_net_eligible_amount(self):
        req=self.mk(headers=self.sales,items=[self.item()])
        res=self.c.put(f"{R}/{req['id']}/discount",json={"amount":"10001"},headers=self.sales)
        self.assertEqual(res.status_code,422,res.text)

    def test_percentage_item_and_request_discounts(self):
        req=self.mk(headers=self.sales,items=[self.item()])
        item_id=req["items"][0]["id"]
        item=self.c.put(f"/api/v1/purchase-request-items/{item_id}/discount",json={"percentage":"10"},headers=self.sales)
        self.assertEqual(item.status_code,200,item.text)
        self.assertEqual(item.json()["data"]["discount_amount"],"1000")
        request=self.c.put(f"{R}/{req['id']}/discount",json={"percentage":"2"},headers=self.sales)
        self.assertEqual(request.status_code,200,request.text)
        self.assertEqual(request.json()["data"]["request_discount_amount"],"180")
