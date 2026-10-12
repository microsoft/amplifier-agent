"""Revenue summaries over recorded sales."""

from inventory import money


def daily_revenue(sales):
    return money.calc_total([sale["amount"] for sale in sales])


def revenue_by_region(sales):
    regions = {}
    for sale in sales:
        regions.setdefault(sale["region"], []).append(sale["amount"])
    return {region: money.calc_total(amounts) for region, amounts in regions.items()}


def summary_line(sales):
    return f"Revenue: {money.calc_total([sale['amount'] for sale in sales]):.2f}"
