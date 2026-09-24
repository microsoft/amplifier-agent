"""summary.json and a self-contained report.html for one run directory."""

from decimal import Decimal
import html
import json
from pathlib import Path
from typing import Any

import yaml

STATUSES = ("passed", "failed", "timeout", "error")
COLUMNS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
    "llm_responses",
    "tool_calls",
    "delegations",
    "turns",
    "agent_wallclock_s",
    "total_wallclock_s",
    "install_s",
)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def source_sha(run_dir: Path) -> str | None:
    for name, key in (("snapshot.json", "head"), ("upstream.json", "sha")):
        data = _read_json(run_dir / name)
        if data:
            return data.get(key)
    return None


def failed_criteria(grader: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every criterion that lost points, flattened across evaluations."""
    rows = []
    for evaluation in (grader or {}).get("evaluations") or []:
        for key, entry in (evaluation.get("failed_criteria") or {}).items():
            rows.append({"evaluation": evaluation.get("name"), "criterion": key} | entry)
    return rows


def grader_usage(trial_dir: Path) -> tuple[str | None, int | None]:
    """(provider/model, tokens in + out) from the trial's grader_result.json, None where unknown."""
    graded = _read_json(trial_dir / "grader" / "grader_result.json")
    if not isinstance(graded, dict):
        return None, None
    info = graded.get("grader") or {}
    model = f"{info.get('provider')}/{info.get('model')}" if info.get("model") else None
    tokens = None
    for entry in (info.get("usage") or {}).get("entries") or []:
        for field in ("tokens_in", "tokens_out"):
            if isinstance(entry.get(field), int):
                tokens = (tokens or 0) + entry[field]
    return model, tokens


def trial_dirs(run_dir: Path) -> list[Path]:
    """Every trial directory under `<run>/trials/`, nested by task group: those holding state.json or trial_result.json."""
    trials = run_dir / "trials"
    found = {path.parent for name in ("state.json", "trial_result.json") for path in trials.glob(f"**/{name}")}
    return sorted(found)


def task_id(trial_dir: Path, result: dict[str, Any] | None) -> str | None:
    """The task id from trial_result.json, else from the rendered task.json."""
    if result and isinstance(result.get("task"), str):
        return result["task"]
    rendered = _read_json(trial_dir / "task.json")
    if isinstance(rendered, dict):
        return (rendered.get("_trial") or {}).get("task")
    return None


def summarize(run_dir: Path) -> dict[str, Any]:
    profile = yaml.safe_load((run_dir / "run.yaml").read_text())
    rows: list[dict[str, Any]] = []
    for trial_dir in trial_dirs(run_dir):
        relative = trial_dir.relative_to(run_dir / "trials").as_posix()
        result = _read_json(trial_dir / "trial_result.json")
        if not isinstance(result, dict):
            missing = (
                "no trial_result.json"
                if not (trial_dir / "trial_result.json").exists()
                else "trial_result.json unreadable"
            )
            rows.append({"dir": relative, "task": task_id(trial_dir, None), "status": "error", "reason": missing})
            continue
        grader = result.get("grader") or {}
        model, tokens = grader_usage(trial_dir)
        rows.append(
            result
            | {
                "dir": relative,
                "task": task_id(trial_dir, result),
                "score": grader.get("overall_score"),
                "pass_score": grader.get("pass_score"),
                "passed": grader.get("passed"),
                "failed_criteria": failed_criteria(grader),
                "grader_model": model,
                "grader_tokens": tokens,
            }
        )
    counts = {status: sum(1 for row in rows if row.get("status") == status) for status in STATUSES}
    cost = Decimal(0)
    cost_known = bool(rows)
    wallclock = 0.0
    for row in rows:
        row_metrics = row.get("metrics") or {}
        value = row_metrics.get("cost_usd")
        if isinstance(value, (int, float)):
            cost += Decimal(str(value))
        else:
            cost_known = False
        if isinstance(row_metrics.get("total_wallclock_s"), (int, float)):
            wallclock += row_metrics["total_wallclock_s"]
    models = sorted({row["grader_model"] for row in rows if row.get("grader_model")})
    tokens = [row["grader_tokens"] for row in rows if isinstance(row.get("grader_tokens"), int)]
    summary = {
        "run": profile["name"],
        "install": profile["install"],
        "agent": profile["agent"],
        "grader": profile["grader"],
        "grader_models": models,
        "grader_total_tokens": sum(tokens) if tokens else "not_available",
        "source_sha": source_sha(run_dir),
        "counts": counts | {"total": len(rows)},
        "total_cost_usd": float(cost) if cost_known else "not_available",
        "total_cost_usd_known_part": float(cost),
        "total_trial_wallclock_s": round(wallclock, 1),
        "trials": rows,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (run_dir / "report.html").write_text(render(summary))
    return summary


def _cell(value: Any) -> str:
    if isinstance(value, float):
        value = f"{value:.6g}"
    return f"<td>{html.escape(str(value))}</td>"


def _score(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "-"


def _details(row: dict[str, Any], width: int) -> str:
    """The expandable findings row under a trial: its reason and every criterion that lost points."""
    failed = row.get("failed_criteria") or []
    reason = row.get("reason")
    if not failed and not reason:
        return ""
    items = "".join(
        f'<li><span class="crit">{html.escape(str(item.get("evaluation")))} / {html.escape(str(item.get("criterion")))}</span>'
        f' <span class="pts">{html.escape(str(item.get("points")))}/{html.escape(str(item.get("max")))}</span>'
        f'<div class="why">{html.escape(str(item.get("reason") or ""))}</div></li>'
        for item in failed
    )
    label = f"{len(failed)} failed criteria" if failed else "details"
    note = (
        f'<div class="why"><b>{html.escape(str(row.get("status")))}:</b> {html.escape(str(reason))}</div>'
        if reason
        else ""
    )
    opened = "" if row.get("status") == "passed" else " open"
    return (
        f'<tr class="detail"><td colspan="{width}"><details{opened}><summary>{html.escape(label)}</summary>'
        f"{note}<ul>{items}</ul></details></td></tr>"
    )


def render(summary: dict[str, Any]) -> str:
    agent = summary["agent"]
    grader = (
        ", ".join(summary.get("grader_models") or []) or f"{summary['grader']['provider']}/{summary['grader']['model']}"
    )
    header_rows = [
        ("run", summary["run"]),
        ("install", summary["install"]),
        ("agent", f"{agent['provider']}/{agent['model']}"),
        ("grader", grader),
        ("grader total tokens", summary.get("grader_total_tokens", "not_available")),
        ("snapshot HEAD" if summary["install"] == "checkout" else "expected v1", summary["source_sha"] or "unknown"),
        ("counts", ", ".join(f"{k} {v}" for k, v in summary["counts"].items())),
        ("total cost USD", summary["total_cost_usd"]),
        ("total trial wallclock s", summary["total_trial_wallclock_s"]),
    ]
    names = ("trial", "status", "score", "pass", "provenance", *COLUMNS)
    body = []
    for row in summary["trials"]:
        row_metrics = row.get("metrics") or {}
        status = html.escape(str(row.get("status")))
        body.append(
            f'<tr class="{status}">'
            f'<td><a href="trials/{html.escape(row["dir"])}/">{html.escape(row["dir"])}</a></td>'
            + _cell(row.get("status"))
            + f"<td>{_score(row.get('score'))}</td>"
            + f"<td>{_score(row.get('pass_score'))}</td>"
            + _cell("yes" if row.get("provenance_ok") else "no")
            + "".join(_cell(row_metrics.get(column, "")) for column in COLUMNS)
            + "</tr>"
            + _details(row, len(names))
        )
    head = "".join(f"<th>{html.escape(name)}</th>" for name in names)
    info = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in header_rows)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(summary["run"])}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2em; }}
table {{ border-collapse: collapse; margin-bottom: 2em; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; font-size: 13px; }}
tr.passed td {{ background: #e6f4ea; }}
tr.failed td {{ background: #fce8e6; }}
tr.timeout td {{ background: #fef7e0; }}
tr.error td {{ background: #f1e6fc; }}
tr.detail td {{ background: #fafafa; }}
details summary {{ cursor: pointer; font-weight: 600; }}
details ul {{ margin: 0.5em 0; padding-left: 1.2em; }}
details li {{ margin-bottom: 0.6em; }}
.crit {{ font-family: ui-monospace, monospace; }}
.pts {{ font-weight: 600; margin-left: 0.4em; }}
.why {{ max-width: 110ch; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.4; margin-top: 0.2em; }}
</style></head><body>
<h1>{html.escape(summary["run"])}</h1>
<table>{info}</table>
<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>
</body></html>
"""
