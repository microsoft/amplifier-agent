"""Money arithmetic shared by orders, invoices, and reports."""


def calc_total(prices, tax_rate=0.0):
    """Return the sum of prices with tax applied, rounded to cents."""
    return round(sum(prices) * (1 + tax_rate), 2)
