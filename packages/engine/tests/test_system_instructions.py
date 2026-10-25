import datetime

from amplifier_agent_engine._engine.configuration import resolve
from amplifier_agent_engine._engine.prompt import (
    AGENTS_PREAMBLE,
    BASE_INSTRUCTIONS,
    system_instructions,
    system_message,
)
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


def test_agents_files_are_appended_from_the_repository_root_to_the_working_directory(tmp_path):
    root = tmp_path / "repo"
    work = root / "service" / "api"
    work.mkdir(parents=True)
    (root / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("Outside the repository\n")
    (root / "AGENTS.md").write_text("Root rule\n\n")
    (root / "service" / "AGENTS.md").write_text("Service rule")
    (work / "AGENTS.md").write_text("API rule\n")
    config = resolve(AgentOptions(working_directory=str(work)))

    text = system_message(config)

    assert text.startswith(system_instructions(config) + "\n\n" + AGENTS_PREAMBLE + "\n\n")
    assert text.endswith(
        f"Instructions from: {root.resolve() / 'AGENTS.md'}\nRoot rule\n\n"
        f"Instructions from: {root.resolve() / 'service' / 'AGENTS.md'}\nService rule\n\n"
        f"Instructions from: {work.resolve() / 'AGENTS.md'}\nAPI rule"
    )
    assert "Outside the repository" not in text


def test_a_git_file_marks_the_repository_root(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Superproject rule")
    module = tmp_path / "module"
    module.mkdir()
    (module / ".git").write_text("gitdir: ../.git/modules/module\n")
    (module / "AGENTS.md").write_text("Module rule")

    text = system_message(resolve(AgentOptions(working_directory=str(module))))

    assert "Module rule" in text
    assert "Superproject rule" not in text


def test_outside_a_repository_only_the_working_directory_is_read(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (tmp_path / "AGENTS.md").write_text("Parent rule")
    (work / "AGENTS.md").write_text("Work rule")

    text = system_message(resolve(AgentOptions(working_directory=str(work))))

    assert text.endswith(f"Instructions from: {work.resolve() / 'AGENTS.md'}\nWork rule")
    assert "Parent rule" not in text


def test_agents_files_are_appended_to_configured_instructions(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Work rule")
    config = resolve(AgentOptions(instructions="Caller instructions", working_directory=str(tmp_path)))

    assert system_instructions(config) == "Caller instructions"
    assert system_message(config) == (
        f"Caller instructions\n\n{AGENTS_PREAMBLE}\n\nInstructions from: {tmp_path.resolve() / 'AGENTS.md'}\nWork rule"
    )


def test_without_agents_files_the_system_message_is_the_instructions(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").mkdir()
    config = resolve(AgentOptions(working_directory=str(tmp_path)))

    assert system_message(config) == system_instructions(config)


def test_empty_agents_files_are_skipped(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text(" \n\t\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / "AGENTS.md").write_text("Work rule")

    text = system_message(resolve(AgentOptions(working_directory=str(work))))

    assert text.count("Instructions from: ") == 1
    assert text.endswith("Work rule")


def test_agents_files_are_read_as_utf8_replacing_invalid_bytes(tmp_path):
    (tmp_path / "AGENTS.md").write_bytes(b"Caf\xc3\xa9 \xff rule")

    assert system_message(resolve(AgentOptions(working_directory=str(tmp_path)))).endswith("Caf\u00e9 \ufffd rule")


def test_the_default_instructions_name_no_other_convention_files():
    assert "CONTRIBUTING.md" not in BASE_INSTRUCTIONS
    assert "README.md" not in BASE_INSTRUCTIONS
    assert "at the end of these instructions" in BASE_INSTRUCTIONS


def test_the_default_instructions_end_with_a_requirement_check_and_leave_delegation_to_the_tool():
    assert "do not cut work short to save time or effort." in BASE_INSTRUCTIONS
    assert "- Do everything the task asks and nothing it does not." in BASE_INSTRUCTIONS
    assert "delegat" not in BASE_INSTRUCTIONS.lower()
    assert BASE_INSTRUCTIONS.endswith(
        "Before you finish, compare what you produced with what the task asked for. When the task states several "
        "specific requirements, such as values, dates, formats, charts, or files to produce, re-read the task and any "
        "files that define it, and confirm each one is met. For a simple request, no separate check is needed. This is "
        "a check of the result, not a new round of testing.\n\n"
        "When you finish, reply with a short summary of what you did and how you verified it."
    )
