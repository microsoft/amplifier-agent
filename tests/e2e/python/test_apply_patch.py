"""apply_patch beside edit_file: which one each model request offers, and its effects on files."""

from amplifier_agent import AgentOptions, ApprovalResponse, SessionOptions, TextPart, TurnInput, create_agent
import pytest

from tests.support.engine import provision, provision_many

EDITORS = {"edit_file", "apply_patch"}


def call(tool_name, **arguments):
    return {"tool": {"name": tool_name, "arguments": arguments}}


def options(provider="anthropic", model="claude-sonnet-5", **values):
    values.setdefault("approvals", "allow")
    return AgentOptions(provider=provider, model=model, **values)


def editors(request):
    return {tool["name"] for tool in request.get("tools") or []} & EDITORS


async def run(session, text="Edit the files."):
    turn = await session.start_turn(TurnInput([TextPart(text)]))
    return [event async for event in turn.events()]


async def run_once(agent, text="Edit the files."):
    async with await agent.create_session(SessionOptions(persistence="ephemeral")) as session:
        return await run(session, text)


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("openai", "gpt-6.1-sol", {"apply_patch"}),
        ("openai", "gpt-5.5", {"apply_patch"}),
        ("anthropic", "claude-sonnet-5", {"edit_file"}),
        ("openai", "gpt-4.1", {"edit_file"}),
        ("openai", "gpt-oss-120b", {"edit_file"}),
        ("openai", "o3-deep-research", {"edit_file"}),
    ],
)
async def test_the_default_set_offers_one_editor_chosen_by_the_model(monkeypatch, provider, model, expected):
    probe = provision(monkeypatch, [{"text": "Listed"}])
    async with await create_agent(options(provider, model)) as agent:
        events = await run_once(agent)
    assert events[-1].payload.state == "success"
    assert editors(probe.requests[0]) == expected


@pytest.mark.parametrize("selected", ["edit_file", "apply_patch"])
@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-4.1"])
async def test_a_single_selected_editor_is_offered_for_every_model(monkeypatch, selected, model):
    probe = provision(monkeypatch, [{"text": "Listed"}])
    async with await create_agent(options("openai", model, tools=["read_file", selected])) as agent:
        events = await run_once(agent)
    assert events[-1].payload.state == "success"
    assert editors(probe.requests[0]) == {selected}


async def test_switching_models_changes_the_offered_editor_on_the_next_request(monkeypatch):
    probe = provision(monkeypatch, [{"text": "First"}, {"text": "Second"}])
    async with (
        await create_agent(options()) as agent,
        await agent.create_session(SessionOptions(persistence="ephemeral")) as session,
    ):
        first = await run(session)
        await session.set_model(provider="openai", model="gpt-6.1-sol")
        second = await run(session)
    assert first[-1].payload.state == second[-1].payload.state == "success"
    assert [editors(request) for request in probe.requests] == [{"edit_file"}, {"apply_patch"}]


async def test_a_delegated_model_chooses_its_own_editor(monkeypatch):
    probes = provision_many(
        monkeypatch,
        [call("delegate", instruction="Edit the files.", model="gpt-5.5"), {"text": "Parent complete"}],
        [{"text": "Child complete"}],
    )
    async with await create_agent(options("openai", "o3-deep-research")) as agent:
        events = await run_once(agent)
    assert events[-1].payload.state == "success", events[-1].payload.error
    parent, child = probes
    assert child.selected_models == ["gpt-5.5"]
    assert editors(parent.requests[0]) == {"edit_file"}
    assert editors(child.requests[0]) == {"apply_patch"}


async def test_a_call_to_the_editor_not_offered_runs_nothing(monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("before\n")
    provision(
        monkeypatch,
        [call("edit_file", file_path="notes.txt", old_string="before", new_string="after"), {"text": "Done"}],
    )
    async with await create_agent(options("openai", "gpt-6.1-sol", working_directory=tmp_path)) as agent:
        events = await run_once(agent)
    assert events[-1].payload.state == "failure"
    assert events[-1].payload.error.code == "provider_failed"
    assert not any(event.type == "tool_call" for event in events)
    assert (tmp_path / "notes.txt").read_text() == "before\n"


async def test_one_patch_updates_and_adds_files_in_the_working_directory(monkeypatch, tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("def total(items):\n    return sum(items)\n\n\nVALUE = total([1, 2])\n")
    (tmp_path / "pkg" / "report.py").write_text("from pkg.core import total\n\nprint(total([3]))\n")
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: pkg/core.py",
            "@@",
            "-def total(items):",
            "+def summed(items):",
            "     return sum(items)",
            "@@",
            "-VALUE = total([1, 2])",
            "+VALUE = summed([1, 2])",
            "*** Update File: pkg/report.py",
            "@@",
            "-from pkg.core import total",
            "+from pkg.core import summed",
            " ",
            "-print(total([3]))",
            "+print(summed([3]))",
            "*** Add File: pkg/extra.py",
            "+EXTRA = 1",
            "*** End Patch",
        ]
    )
    provision(monkeypatch, [call("apply_patch", patch=patch), {"text": "Done"}])
    async with await create_agent(options("openai", "gpt-6.1-sol", working_directory=tmp_path)) as agent:
        events = await run_once(agent)
    assert events[-1].payload.state == "success", events[-1].payload.error
    resolution = next(event.payload.resolution for event in events if event.type == "tool_result")
    assert resolution.outcome == "completed"
    assert resolution.content == "M pkg/core.py\nM pkg/report.py\nA pkg/extra.py"
    assert (tmp_path / "pkg" / "core.py").read_text() == (
        "def summed(items):\n    return sum(items)\n\n\nVALUE = summed([1, 2])\n"
    )
    assert (tmp_path / "pkg" / "report.py").read_text() == "from pkg.core import summed\n\nprint(summed([3]))\n"
    assert (tmp_path / "pkg" / "extra.py").read_text() == "EXTRA = 1"


async def test_a_patch_outside_the_allowed_directories_fails_without_writing(monkeypatch, tmp_path):
    project = tmp_path / "project"
    shared = tmp_path / "shared"
    project.mkdir()
    shared.mkdir()
    outside = tmp_path / "outside.txt"
    patches = [
        f"*** Begin Patch\n*** Add File: {shared / 'shared.txt'}\n+shared\n*** End Patch",
        f"*** Begin Patch\n*** Add File: {outside}\n+outside\n*** End Patch",
        "*** Begin Patch\n*** Add File: ../escaped.txt\n+escaped\n*** End Patch",
    ]
    provision(monkeypatch, [*(call("apply_patch", patch=patch) for patch in patches), {"text": "Done"}])
    agent_options = options(
        "openai",
        "gpt-6.1-sol",
        working_directory=project,
        additional_directories=[shared],
        tool_error_policy="continue",
    )
    async with await create_agent(agent_options) as agent:
        events = await run_once(agent)
    assert events[-1].payload.state == "success", events[-1].payload.error
    shared_result, outside_result, escaped_result = (
        event.payload.resolution for event in events if event.type == "tool_result"
    )
    assert shared_result.outcome == "completed"
    assert (shared / "shared.txt").read_text() == "shared"
    for refused in (outside_result, escaped_result):
        assert refused.outcome == "failed"
        assert refused.error is not None
        assert "not within allowed write paths" in refused.error.message
    assert not outside.exists()
    assert not (tmp_path / "escaped.txt").exists()


async def test_the_approval_preview_of_a_patch_shows_the_patch(monkeypatch, tmp_path):
    patch = "*** Begin Patch\n*** Add File: new.txt\n+new\n*** End Patch"
    provision(monkeypatch, [call("apply_patch", patch=patch), {"text": "Done"}])
    summaries = []

    async def approve(request):
        summaries.append(request.summary)
        return ApprovalResponse("deny")

    async with await create_agent(
        options("openai", "gpt-6.1-sol", approvals=approve, working_directory=tmp_path)
    ) as agent:
        events = await run_once(agent)
    assert events[-1].payload.state == "success"
    assert len(summaries) == 1
    assert 'Run built-in tool "apply_patch".' in summaries[0]
    assert '{"patch": "*** Begin Patch\\n*** Add File: new.txt' in summaries[0]
    assert not (tmp_path / "new.txt").exists()
