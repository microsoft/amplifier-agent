"""Every prompt template declares exactly what it uses and renders under strict undefined."""

from typing import Any

from amplifier_agent_grader import prompts
import pytest

NAMES = sorted(path.stem for path in prompts.PROMPTS.glob("*.md"))
LAYOUT = {
    "workspace": "/workspace",
    "driver": "/home/agent/app/out",
    "task": "/home/agent/grader/task.json",
    "sessions": "/home/agent/.amplifier-agent",
    "grader_data": "/home/agent/grader/data",
    "scratch": "/home/agent/grader/scratch",
}
SAMPLES: dict[str, Any] = {
    "task": {"name": "hello", "description": "d", "turns": [{"user": "hi"}]},
    "replies": [{"segment": 0, "index": 0, "state": "success", "content": "ready", "error": None}],
    "layout": LAYOUT,
    "evaluation": {
        "name": "reply",
        "steps": "1. Step one.",
        "rubric": [{"key": "a", "points": 10, "description": "x"}],
    },
    "submit_tool": "submit_rubric",
    "reason": "tool failed",
}


def test_prompt_set() -> None:
    assert NAMES == ["report", "report_retry", "submit", "submit_retry", "system"]


@pytest.mark.parametrize("name", NAMES)
def test_template_uses_only_declared_variables(name: str) -> None:
    prompt = prompts.load(name)
    assert sorted(prompt.template.global_variables()) == sorted(prompt.variables)


@pytest.mark.parametrize("name", NAMES)
def test_template_renders(name: str) -> None:
    prompt = prompts.load(name)
    text = prompts.render(name, **{key: SAMPLES[key] for key in prompt.variables})
    assert text
    assert "{{" not in text
    assert "{%" not in text


def test_render_rejects_missing_and_extra_variables() -> None:
    with pytest.raises(prompts.PromptError, match="missing variables"):
        prompts.render("submit_retry", reason="r")
    with pytest.raises(prompts.PromptError, match="undeclared variables"):
        prompts.render("report", reason="r")


def test_strict_undefined_fails_on_unknown_fields() -> None:
    task = {key: value for key, value in SAMPLES["task"].items() if key != "turns"}
    with pytest.raises(Exception, match="undefined"):
        prompts.render("system", **{key: SAMPLES[key] for key in prompts.load("system").variables} | {"task": task})


def test_system_prompt_fills_layout_and_rules() -> None:
    text = prompts.render("system", **{key: SAMPLES[key] for key in prompts.load("system").variables})
    assert "never read events.jsonl whole" in text.lower()
    assert 'jq -c \'select(.type=="tool_call")' in text
    assert "/home/agent/app/out/events.jsonl" in text
    assert "/home/agent/grader/scratch" in text
    assert "scores 0" in text
    assert "no\nsympathy points" in text or "no sympathy points" in text
    assert '"user": "hi"' in text
    assert "- a (max 10 points): x" in text
    assert "EVENTS" in text


def test_front_matter_is_required() -> None:
    with pytest.raises(prompts.PromptError):
        prompts.parse("x", "no front matter", prompts.environment())
    with pytest.raises(prompts.PromptError):
        prompts.parse("x", "---\nother: 1\n---\nbody", prompts.environment())
