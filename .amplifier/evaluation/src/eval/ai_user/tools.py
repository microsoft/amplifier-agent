"""Tools the AI User session calls to signal completion.

The AI User talks to the agent through Foundation's built-in `bash` tool,
guided by a per-agent invocation guide. The only tool the harness adds is
`conclude`, which captures the AI User's verdict when the scenario is done.

Copied from the reference library (`amplifier_evaluation.ai_user.tools`): the
conclude contract is agent- and task-agnostic, so it carries over unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from amplifier_core import ToolResult


@dataclass
class ConcludeResult:
    """Captured by ConcludeTool when the AI User signals it is done."""

    verdict: str  # success | partial | failure | give_up
    reasoning: str
    summary: str


class ConcludeTool:
    """Signal that the interaction is complete and record the verdict.

    Does not raise or short-circuit the orchestrator. The session is
    expected to stop calling tools after this and return its final
    assistant message naturally.
    """

    def __init__(self) -> None:
        self.result: ConcludeResult | None = None

    @property
    def name(self) -> str:
        return "conclude"

    @property
    def description(self) -> str:
        return (
            "Signal that the interaction is finished. Call this when the "
            "scenario is complete, has failed, or the agent is stuck. "
            "After calling this you do not need to send more messages and "
            "you do not need to write a long final reply."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["success", "partial", "failure", "give_up"],
                    "description": (
                        "Overall result: success (scenario completed), "
                        "partial (some but not all), failure (agent did the "
                        "wrong thing or errored), give_up (stuck and unable "
                        "to make progress)."
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Brief justification for the verdict, citing what the agent actually did."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "One-paragraph summary of what happened during the interaction."
                    ),
                },
            },
            "required": ["verdict", "reasoning", "summary"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        self.result = ConcludeResult(
            verdict=input["verdict"],
            reasoning=input["reasoning"],
            summary=input["summary"],
        )
        return ToolResult(
            success=True,
            output="Conclusion recorded. You are done. No need to send more messages.",
        )
