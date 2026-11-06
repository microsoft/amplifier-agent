"""Binding failures and the face's own refusals as JSON-RPC errors (acp-face.v1 section 4)."""

from acp import RequestError
from amplifier_agent import AgentError


def request_error(error: AgentError) -> RequestError:
    code = (
        -32602
        if error.code in ("invalid_input", "session_id_invalid")
        else -32002
        if error.code == "not_found"
        else -32603
    )
    return RequestError(
        code,
        f"{error.message} {error.remedy}",
        {"code": error.code, "category": error.category, "retryable": error.retryable},
    )


def refusal(message: str, remedy: str) -> RequestError:
    """The face's own refusal of a value the client sent."""
    return request_error(AgentError("invalid_input", "input", message, remedy))


def busy(session_id: str) -> RequestError:
    return request_error(
        AgentError(
            "busy",
            "turn",
            f"Session '{session_id}' has a turn in progress.",
            "Wait for the turn to end, or cancel it with session/cancel.",
        )
    )


def not_held(session_id: str) -> RequestError:
    return request_error(
        AgentError(
            "not_found",
            "session",
            f"Session '{session_id}' is not open in this agent.",
            "Open it with session/load or session/resume first.",
        )
    )


def internal() -> RequestError:
    return request_error(
        AgentError(
            "internal_failed",
            "internal",
            "The request could not complete.",
            "Check the agent's stderr before retrying.",
        )
    )


def closing() -> RequestError:
    return request_error(
        AgentError(
            "closed",
            "lifecycle",
            "The agent is shutting down because its client closed stdin.",
            "Start the agent again and send the prompt to the new process.",
        )
    )
