"""A small function with a couple of debatable design choices."""


def load_config(path):
    # No input validation on path. Broad except that swallows all errors and
    # silently returns an empty dict. Debatable choices worth multiple review
    # lenses (security, correctness, maintainability, error handling).
    try:
        with open(path) as f:
            data = f.read()
        result = {}
        for line in data.splitlines():
            key, value = line.split("=")
            result[key] = value
        return result
    except Exception:
        return {}
