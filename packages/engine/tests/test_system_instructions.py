import datetime

from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._engine.prompt import BASE_INSTRUCTIONS, system_instructions
from amplifier_agent_engine._records import AgentOptions


def test_default_instructions_end_with_the_environment(tmp_path):
    (tmp_path / ".git").mkdir()
    work = tmp_path / "work"
    extra = tmp_path / "extra"
    work.mkdir()
    extra.mkdir()
    config = resolve(AgentOptions(working_directory=str(work), additional_directories=[str(extra)]))

    text = system_instructions(config)

    assert text.startswith(BASE_INSTRUCTIONS)
    assert f"Working directory: {work.resolve()}" in text
    assert f"Additional directories: {extra.resolve()}" in text
    assert "Git repository: yes" in text
    assert text.endswith(f"Date: {datetime.date.today().isoformat()}")


def test_configured_instructions_replace_the_default(tmp_path):
    config = resolve(AgentOptions(instructions="Caller instructions", working_directory=str(tmp_path)))

    assert system_instructions(config) == "Caller instructions"
