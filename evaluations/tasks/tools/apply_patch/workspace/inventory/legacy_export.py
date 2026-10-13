"""Fixed-width export for the retired warehouse system."""


def export_rows(rows):
    return "\n".join(f"{row['sku']:<12}{row['quantity']:>6}" for row in rows)
