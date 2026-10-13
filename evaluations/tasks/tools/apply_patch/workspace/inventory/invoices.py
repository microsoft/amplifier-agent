"""Totals for an invoice covering several orders."""

from inventory.money import calc_total
from inventory.orders import order_total


def invoice_total(orders, tax_rate):
    return calc_total([order_total(order, tax_rate) for order in orders])


def credit_total(credits):
    return calc_total(credits)


def balance_due(orders, credits, tax_rate):
    return round(invoice_total(orders, tax_rate) - calc_total(credits), 2)
