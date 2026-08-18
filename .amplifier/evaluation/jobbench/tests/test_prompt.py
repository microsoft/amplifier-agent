"""Golden-hash lock on jobbench.prompt.render() -- the agent-facing wording.

prompt.py is reproduced verbatim from the JobBench reference runners, and its
own docstring says why that matters: scores produced under a different prompt
are not comparable to any other run of this benchmark, published or local.
A reworded prompt does not fail -- it produces plausible numbers that silently
mean something else. Nothing else in the harness would notice.

So the wording is pinned by hash. The only thing that may legitimately vary
is the three interpolated paths, which differ from upstream because trials run
inside a container rather than a host /tmp directory.

The prompt is the harness's own wrapper text, not JobBench task or rubric
content: it points the agent AT its task folder and deliberately does not
inline TASK_INSTRUCTIONS.txt. No benchmark content appears in this file.
"""

from __future__ import annotations

import hashlib

from jobbench import prompt

# sha256 of `prompt.render()` with its default (container) paths.
#
# If this test fails you have changed the prompt handed to every agent under
# test. That is not a formatting detail. Every score this harness has ever
# published was produced under the OLD wording, so the new wording silently
# invalidates cross-run comparability: old and new numbers can still be
# averaged, charted, and compared, and they will be meaningless together.
# Upstream's published JobBench numbers become incomparable too.
#
# Update this constant ONLY when you intend that, and say so in the commit
# message along with which runs are no longer comparable. If you are here
# because of a typo fix or a lint autofix, revert the source change instead.
GOLDEN_SHA256 = "cc5ff13cf7a08ed34ac47dc37b0ff4173490ca83393c85de2c4900df57c0190b"

_WHY = (
    "The agent-facing prompt wording changed. Scores produced under a "
    "different prompt are NOT comparable to any other run of this benchmark, "
    "published or local -- and nothing else in the harness will notice, "
    "because a reworded prompt still produces plausible-looking numbers. "
    "If the change was intentional, update GOLDEN_SHA256 and record which "
    "prior runs stop being comparable. Otherwise revert src/jobbench/prompt.py."
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_rendered_prompt_matches_the_golden_hash():
    assert _sha(prompt.render()) == GOLDEN_SHA256, _WHY


def test_template_is_stable_across_calls():
    """render() is pure -- no timestamp, uuid, or env-dependent content leaks
    into the bytes sent to the agent, or two trials of the same task would
    not be running the same benchmark.
    """
    assert prompt.render() == prompt.render()


# ---------------------------------------------------------------------------
# The three interpolated paths -- the only part that may legitimately vary
# ---------------------------------------------------------------------------


def test_default_paths_are_the_container_layout():
    assert prompt.WORKSPACE == "/workspace"
    assert prompt.TASK_FOLDER == "/workspace/task_folder"
    assert prompt.OUTPUT_DIR == "/workspace/output"
    assert prompt.PROMPT_PATH == "/workspace/prompt.txt"


def test_task_folder_lands_in_both_places_it_is_named():
    """Once under the TASK FOLDER header, once in the IMPORTANT reminder."""
    text = prompt.render(task_folder="/synthetic/tasks")
    assert text.count("/synthetic/tasks") == 2
    assert text.startswith("=== TASK FOLDER ===\n/synthetic/tasks\n")
    assert "All reference files are in the task folder: /synthetic/tasks" in text


def test_output_dir_lands_in_both_places_it_is_named():
    """Once under the OUTPUT DIRECTORY header, once in the IMPORTANT reminder.

    Both must agree: an agent told two different output paths writes its
    deliverables where the harness will not pull them, and the trial grades
    as no_deliverables through no fault of the agent.
    """
    text = prompt.render(output_dir="/synthetic/out")
    assert text.count("/synthetic/out") == 2
    assert "=== OUTPUT DIRECTORY ===\n/synthetic/out\n" in text
    assert "Only save the final deliverables to the output directory /synthetic/out" in text


def test_workspace_scopes_the_filesystem_restriction():
    """The workspace path appears exactly once, in the access restriction --
    the sentence that keeps the agent out of the rest of the container.
    """
    text = prompt.render(workspace="/synthetic/ws")
    assert text.count("/synthetic/ws") == 1
    assert "You MUST only access files within /synthetic/ws" in text


def test_all_three_paths_are_independently_substituted():
    text = prompt.render(
        workspace="/ws-only",
        task_folder="/task-only",
        output_dir="/out-only",
    )
    assert text.count("/ws-only") == 1
    assert text.count("/task-only") == 2
    assert text.count("/out-only") == 2
    # No placeholder survives unsubstituted.
    assert "{" not in text
    assert "}" not in text


def test_task_instructions_are_not_inlined():
    """Upstream points the agent AT the instructions file and expects it to
    read it, so navigating its own workspace is part of what is measured.
    Inlining the text would change what the benchmark tests -- and would put
    real task content into the harness's own prompt.
    """
    text = prompt.render()
    assert "TASK_INSTRUCTIONS.txt" in text
    assert "Read the TASK_INSTRUCTIONS.txt file in the task folder above" in text
