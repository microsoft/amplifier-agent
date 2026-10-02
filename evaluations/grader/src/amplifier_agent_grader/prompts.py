"""The grader's prompts: one Liquid template per file in prompts/, with front matter declaring its variables.

A prompt file is

    ---
    variables: [name, ...]
    ---
    template body

Rendering is strict: every declared variable must be passed, nothing else may be, and a reference to anything
undefined fails. `json` is the one filter added to Liquid's built-ins; it renders a value as indented JSON.
"""

from dataclasses import dataclass
import functools
import json
from pathlib import Path
from typing import Any

from liquid import BoundTemplate, Environment, StrictUndefined
import yaml

PROMPTS = Path(__file__).parent / "prompts"
FENCE = "---\n"


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class Prompt:
    name: str
    variables: tuple[str, ...]
    template: BoundTemplate


def environment() -> Environment:
    env = Environment(undefined=StrictUndefined)
    env.filters["json"] = lambda value: json.dumps(value, indent=2)
    return env


def parse(name: str, text: str, env: Environment) -> Prompt:
    if not text.startswith(FENCE) or FENCE not in text[len(FENCE) :]:
        raise PromptError(f"{name}: a prompt starts with front matter between --- lines")
    header, body = text[len(FENCE) :].split(FENCE, 1)
    front = yaml.safe_load(header) or {}
    variables = front.get("variables") if isinstance(front, dict) else None
    if not isinstance(variables, list) or not all(isinstance(item, str) for item in variables):
        raise PromptError(f"{name}: front matter needs variables, a list of names")
    return Prompt(name, tuple(variables), env.from_string(body))


@functools.cache
def load(name: str, root: Path = PROMPTS) -> Prompt:
    """The prompt `<root>/<name>.md`."""
    return parse(name, (root / f"{name}.md").read_text(encoding="utf-8"), environment())


def render(name: str, **variables: Any) -> str:
    """Render prompt `name` with exactly its declared variables."""
    prompt = load(name)
    missing = sorted(set(prompt.variables) - set(variables))
    extra = sorted(set(variables) - set(prompt.variables))
    if missing or extra:
        raise PromptError(f"{name}: missing variables {missing}, undeclared variables {extra}")
    return prompt.template.render(**variables).strip()
