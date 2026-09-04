"""Independent deterministic engine for public ready, reply, error, and close acceptance."""

import copy
import uuid

from amplifier_agent_engine._records import (
    AgentError,
    Event,
    OutputDelta,
    Selection,
    SessionRecord,
    TextPart,
    TurnInfo,
    TurnRecord,
    TurnResult,
    TurnStarted,
)


def _closed():
    return AgentError("closed", "lifecycle", "The handle is closed.", "Create a new handle.")


class ReplacementTurn:
    def __init__(self, session, input):
        self.session = session
        self.info = TurnInfo(session.info.session_id, uuid.uuid4().hex)
        self.consumed = False
        messages = [*input.content]
        for message in input.history or []:
            messages.extend(message.content)
        prompt = "".join(part.text for part in messages)
        if "Fail the provider" in prompt:
            result = TurnResult(
                "failure",
                error=AgentError(
                    "provider_failed",
                    "provider",
                    "The deterministic provider failed.",
                    "Use an available provider.",
                    False,
                ),
            )
            deltas = []
        else:
            deltas = (
                ["History preserved"] if "Preserve this history" in prompt else ["Hello ", "world"]
            )
            result = TurnResult("success", content=[TextPart(text) for text in deltas])
        self.result = result
        payloads = [
            ("turn_started", TurnStarted("fresh", Selection("anthropic", "claude-sonnet-5")))
        ]
        payloads.extend(("output_delta", OutputDelta([TextPart(text)])) for text in deltas)
        payloads.append(("terminal", result))
        self._events = [
            Event("turn-events/1", self.info.session_id, self.info.turn_id, sequence, kind, payload)
            for sequence, (kind, payload) in enumerate(payloads, 1)
        ]
        session._history.append(
            TurnRecord(self.info.turn_id, copy.deepcopy(input), copy.deepcopy(result))
        )
        self._final_history = copy.deepcopy(session._history)

    @property
    def final_history(self):
        return copy.deepcopy(self._final_history)

    async def events(self):
        if self.consumed:
            raise AgentError(
                "stream_already_consumed",
                "turn",
                "The stream already has a consumer.",
                "Read each stream once.",
            )
        self.consumed = True
        for event in self._events:
            yield copy.deepcopy(event)

    async def cancel(self):
        return None


class ReplacementSession:
    def __init__(self):
        self.info = SessionRecord(uuid.uuid4().hex, "ephemeral")
        self._history = []
        self.closed = False

    @property
    def history(self):
        return copy.deepcopy(self._history)

    async def start_turn(self, input):
        if self.closed:
            raise _closed()
        return ReplacementTurn(self, input)

    async def run(self, input):
        result, _ = await self.run_with_history(input)
        return result

    async def run_with_history(self, input):
        turn = await self.start_turn(input)
        return copy.deepcopy(turn.result), turn.final_history

    async def close(self):
        self.closed = True


class ReplacementAgent:
    contract_versions = (
        "agent-interface/1",
        "turn-events/1",
        "language-binding/1",
        "host-config/1",
    )

    def __init__(self):
        self.sessions = []
        self.closed = False

    async def create_session(self, options=None):
        if self.closed:
            raise _closed()
        session = ReplacementSession()
        self.sessions.append(session)
        return session

    async def close(self):
        self.closed = True
        for session in self.sessions:
            await session.close()


async def create_engine(options):
    return ReplacementAgent()
