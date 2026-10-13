"""Totals for a single order."""

from inventory.money import calc_total


def line_amounts(order):
    return [line["price"] * line["quantity"] for line in order["lines"]]


def order_subtotal(order):
    return calc_total(line_amounts(order))


def order_total(order, tax_rate):
    return calc_total(line_amounts(order), tax_rate)


def shipping_total(order):
    return calc_total([order.get("shipping", 0.0)])
