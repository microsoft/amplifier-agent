"""Evolve valid emitted records and callback metadata for lossless binding observations."""

import copy
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

RECORDS = json.loads((Path(__file__).parents[1] / "scenarios" / "records.json").read_text())


def install(monkeypatch=None):
    from amplifier_agent_engine._engine import assembly, state, tools
    from amplifier_agent_engine._records import AgentError, Progress

    initialize = state.EngineTurn.__init__
    work_started = state.EngineTurn.work_started
    prepare_tools = tools.prepare_tools
    create_engine = assembly.create_engine
    emit = state.EngineTurn.emit

    def fixture_error():
        fields = copy.deepcopy(RECORDS["error"])
        extra = fields.pop("org.example.detail")
        error = AgentError(**fields)
        setattr(error, "org.example.detail", extra)
        return error

    async def construct(options):
        if options.instructions == RECORDS["method_error_marker"]:
            raise fixture_error()
        return await create_engine(options)

    def emit_record(turn, name, payload):
        if name == "terminal" and any(
            part.text == RECORDS["terminal_error_marker"] for part in turn.input.content
        ):
            payload.state = "failure"
            payload.error = fixture_error()
        emit(turn, name, payload)

    def enabled(turn):
        return any(RECORDS["marker"] in part.text for part in turn.input.content)

    def initialize_turn(turn, session, input, model):
        initialize(turn, session, input, model)
        if not enabled(turn):
            return
        append = turn._journal.append

        def append_evolved(event):
            event.at = datetime.fromisoformat(RECORDS["at"].replace("Z", "+00:00"))
            setattr(event, "org.example.envelope", copy.deepcopy(RECORDS["envelope_extension"]))
            if hasattr(event.payload, "__dict__"):
                setattr(
                    event.payload, "future_optional", copy.deepcopy(RECORDS["payload_extension"])
                )
                setattr(
                    event.payload,
                    "org.example.payload",
                    copy.deepcopy(RECORDS["payload_extension"]),
                )
            append(event)

        turn._journal.append = append_evolved

    def start_work(turn, provider=None, model=None):
        work_started(turn, provider, model)
        if enabled(turn):
            turn.emit("progress", Progress(copy.deepcopy(RECORDS["progress"])))

    async def prepare(runtime):
        registry = await prepare_tools(runtime)
        name = "conformance_records"
        if name in registry.tools:
            registry.tools[name] = replace(
                registry.tools[name],
                deadline=datetime.fromisoformat(RECORDS["deadline"].replace("Z", "+00:00")),
            )
        return registry

    patch = monkeypatch.setattr if monkeypatch is not None else setattr
    patch(state.EngineTurn, "__init__", initialize_turn)
    patch(state.EngineTurn, "work_started", start_work)
    patch(state.EngineTurn, "emit", emit_record)
    patch(tools, "prepare_tools", prepare)
    patch(assembly, "create_engine", construct)
