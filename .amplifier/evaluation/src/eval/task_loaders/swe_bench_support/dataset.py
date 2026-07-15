"""Fetch a single SWE-bench Pro instance row on the fly from HuggingFace.

No dataset content is stored in this repo. Given an instance_id, this reads the
public ScaleAI/SWE-bench_Pro parquet directly from HuggingFace and returns the
row as a dict. List-valued fields that the dataset stores as JSON strings
(selected_test_files_to_run, fail_to_pass, pass_to_pass) are parsed to Python
lists via the helpers below.

Copy-adapted verbatim from the proven prior-art swe_bench_pro package.
"""

from __future__ import annotations

import ast
import functools
import json
import urllib.request
from typing import Any

DATASET = "ScaleAI/SWE-bench_Pro"
_PARQUET_API = f"https://datasets-server.huggingface.co/parquet?dataset={DATASET}"


@functools.lru_cache(maxsize=1)
def _test_parquet_urls() -> tuple[str, ...]:
    """Discover the public parquet file URL(s) for the test split."""
    with urllib.request.urlopen(_PARQUET_API, timeout=60) as resp:
        data = json.load(resp)
    urls = [f["url"] for f in data.get("parquet_files", []) if f.get("split") == "test"]
    if not urls:
        raise RuntimeError(f"No 'test' parquet files found for {DATASET}")
    return tuple(urls)


def fetch_instance(instance_id: str) -> dict[str, Any]:
    """Return the raw dataset row for ``instance_id`` as a dict.

    Reads the remote parquet with duckdb, filtering server-side so only the one
    matching row is materialized.
    """
    import duckdb  # lazy import so merely importing this module stays cheap

    urls = _test_parquet_urls()
    url_list = ", ".join(f"'{u}'" for u in urls)
    con = duckdb.connect()
    try:
        try:
            con.execute("INSTALL httpfs; LOAD httpfs;")
        except Exception:
            pass  # recent duckdb auto-loads httpfs for https reads
        rel = con.execute(
            f"SELECT * FROM read_parquet([{url_list}]) WHERE instance_id = ? LIMIT 1",
            [instance_id],
        )
        columns = [d[0] for d in rel.description]
        row = rel.fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"instance_id not found in {DATASET}: {instance_id}")
    return dict(zip(columns, row, strict=True))


def as_list(value: Any) -> list[str]:
    """Parse a dataset field that may be a JSON-encoded list or already a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    text = str(value).strip()
    if not text:
        return []
    # These fields are stored as stringified lists. Some rows use valid JSON,
    # others use Python-repr with single quotes (or a mix), so fall back to
    # ast.literal_eval which accepts both quote styles.
    try:
        return list(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        return list(ast.literal_eval(text))


def last_setup_line(before_repo_set_cmd: str) -> str:
    """Final line of before_repo_set_cmd: checks out the held-out test files."""
    lines = [ln for ln in (before_repo_set_cmd or "").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


if __name__ == "__main__":
    import sys

    iid = sys.argv[1]
    row = fetch_instance(iid)
    print("keys:", sorted(row.keys()))
    print("repo:", row.get("repo"))
    print("repo_language:", row.get("repo_language"))
    print("base_commit:", row.get("base_commit"))
    print("dockerhub_tag:", row.get("dockerhub_tag"))
    print("selected_test_files_to_run:", as_list(row.get("selected_test_files_to_run")))
    print("fail_to_pass:", as_list(row.get("fail_to_pass")))
    print("pass_to_pass (count):", len(as_list(row.get("pass_to_pass"))))
    print("problem_statement (head):", (row.get("problem_statement") or "")[:160])
    print("last_setup_line:", last_setup_line(row.get("before_repo_set_cmd", "")))
