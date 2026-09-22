from pathlib import Path
from types import SimpleNamespace

import pytest
from amplifier_agent_engine._engine.builtin_tools import builtin_tools
from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._records import BUILTIN_TOOLS, AgentError, AgentOptions, Tool

SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}


@pytest.fixture(autouse=True)
def isolated_host(monkeypatch, tmp_path):
    host = tmp_path / "host.json"
    host.write_text("{}")
    monkeypatch.setenv("AMPLIFIER_AGENT_CONFIG", str(host))


async def handler(arguments, context):
    return "handled"


def declared(name):
    return Tool(name, f"Caller {name}.", dict(SCHEMA), handler)


def test_the_constant_names_the_nine_built_ins_in_contract_order():
    assert BUILTIN_TOOLS == (
        "read_file", "write_file", "edit_file", "glob", "grep", "bash", "web_fetch",
        "web_search", "delegate",
    )


def test_the_engine_constructs_exactly_the_named_built_ins():
    runtime = SimpleNamespace(
        config=SimpleNamespace(working_directory=Path.cwd()),
        core=SimpleNamespace(coordinator=None),
    )
    names = [tool.name for tool in builtin_tools(runtime)]
    assert sorted(names) == sorted(BUILTIN_TOOLS)
    assert all(tool.source == "built-in" for tool in builtin_tools(runtime))


@pytest.mark.parametrize(("tools", "builtins", "callers"), [
    (None, BUILTIN_TOOLS, ()),
    ([], (), ()),
    (["read_file", "grep"], ("read_file", "grep"), ()),
    ([declared("bash")], (), ("bash",)),
    (["read_file", declared("note")], ("read_file",), ("note",)),
    ([*BUILTIN_TOOLS, declared("note")], BUILTIN_TOOLS, ("note",)),
])
def test_the_tool_set_selects_built_ins_by_name_beside_caller_declarations(
    tools, builtins, callers,
):
    config = resolve(AgentOptions(tools=tools))
    assert config.builtin_tools == builtins
    assert tuple(tool.name for tool in config.tools) == callers


@pytest.mark.parametrize(("tools", "field"), [
    (["shell"], "tools[0]"),
    (["read_file", "Read_File"], "tools[1]"),
    (["grep", "grep"], "tools[1]"),
    (["bash", declared("bash")], "tools[1].name"),
    ([declared("bash"), "bash"], "tools[1]"),
    ([declared("note"), declared("note")], "tools[1].name"),
    (("read_file",), "tools"),
    ("read_file", "tools"),
    ([True], "tools[0]"),
    ([7], "tools[0]"),
    ([None], "tools[0]"),
])
def test_an_invalid_tool_set_is_refused_by_field(tools, field):
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions(tools=tools))
    assert caught.value.code == "invalid_input"
    assert caught.value.details == {"field": field}
    assert caught.value.remedy


def test_an_unknown_name_is_refused_with_a_remedy_naming_the_constant():
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions(tools=["shell"]))
    assert "'shell'" in caught.value.message
    assert "BUILTIN_TOOLS" in caught.value.remedy


def test_a_caller_declaration_without_a_handler_is_refused():
    tool = declared("note")
    tool.handler = None
    with pytest.raises(AgentError) as caught:
        resolve(AgentOptions(tools=["read_file", tool]))
    assert caught.value.details == {"field": "tools[1]"}
