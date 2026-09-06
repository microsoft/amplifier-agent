import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_typescript_reporter_retains_named_failures_skips_and_locations():
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node is required to verify TypeScript conformance reporting.")
    script = """
import report from './conformance/typescript_reporter.mjs';
const events = [
  {type: 'test:pass', data: {name: 'passing assertion', file: 'test/contracts.test.ts', line: 4, column: 1, nesting: 0, details: {type: 'test'}}},
  {type: 'test:fail', data: {name: 'broken assertion', file: 'test/contracts.test.ts', line: 7, nesting: 0, details: {error: {message: 'failed', failureType: 'testCodeFailure', cause: {message: 'missing terminal'}}}}},
  {type: 'test:pass', data: {name: 'skipped assertion', skip: true, nesting: 0}},
  {type: 'test:pass', data: {name: 'unfinished assertion', todo: true, nesting: 0}},
  {type: 'test:summary', data: {success: false, counts: {tests: 4, passed: 1, failed: 1, skipped: 1, todo: 1}}},
];
for await (const line of report(events)) process.stdout.write(line);
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT, capture_output=True, text=True, timeout=10, check=True,
    )
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [record.get("status") for record in records[:4]] == [
        "passed", "failed", "skipped", "skipped",
    ]
    assert records[0] == {
        "kind": "case", "case": "passing assertion", "status": "passed",
        "file": "test/contracts.test.ts", "line": 4, "column": 1,
        "nesting": 0, "type": "test",
    }
    assert records[1]["case"] == "broken assertion"
    assert records[1]["failure"] == {
        "message": "failed", "type": "testCodeFailure", "cause": "missing terminal",
    }
    assert records[-1] == {
        "kind": "summary", "success": False,
        "counts": {"tests": 4, "passed": 1, "failed": 1, "skipped": 1, "todo": 1},
    }
