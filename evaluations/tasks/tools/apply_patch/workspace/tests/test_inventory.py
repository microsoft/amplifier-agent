import unittest

from inventory import invoices, orders, reports
from inventory.money import calc_total

ORDER = {"lines": [{"price": 2.5, "quantity": 4}, {"price": 1.25, "quantity": 2}], "shipping": 4.0}
SALES = [
    {"region": "north", "amount": 10.0},
    {"region": "south", "amount": 2.5},
    {"region": "north", "amount": 5.0},
]


class MoneyTest(unittest.TestCase):
    def test_total_applies_tax(self):
        self.assertEqual(calc_total([10.0, 5.0], 0.1), 16.5)


class OrdersTest(unittest.TestCase):
    def test_subtotal_and_total(self):
        self.assertEqual(orders.order_subtotal(ORDER), 12.5)
        self.assertEqual(orders.order_total(ORDER, 0.2), 15.0)
        self.assertEqual(orders.shipping_total(ORDER), 4.0)


class InvoicesTest(unittest.TestCase):
    def test_balance_due_subtracts_credits(self):
        self.assertEqual(invoices.invoice_total([ORDER, ORDER], 0.0), 25.0)
        self.assertEqual(invoices.credit_total([1.0, 2.0]), 3.0)
        self.assertEqual(invoices.balance_due([ORDER], [2.5], 0.0), 10.0)


class ReportsTest(unittest.TestCase):
    def test_revenue(self):
        self.assertEqual(reports.daily_revenue(SALES), 17.5)
        self.assertEqual(reports.revenue_by_region(SALES), {"north": 15.0, "south": 2.5})
        self.assertEqual(reports.summary_line(SALES), "Revenue: 17.50")


if __name__ == "__main__":
    unittest.main()
