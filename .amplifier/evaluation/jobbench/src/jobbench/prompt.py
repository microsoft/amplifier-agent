# Portions of this file are derived from github.com/Job-Bench/job-bench-eval,
# licensed under the Apache License, Version 2.0.
# The `_TEMPLATE` string below is reproduced verbatim from upstream's
# eval/run_benchmark_codex_cli.sh (the `prompt_msg` assignment), where it is
# byte-identical across all three reference runners. Modification by Microsoft
# Corporation: the three interpolated paths (workspace, task_folder,
# output_dir) point at the container-side layout in WORKSPACE/TASK_FOLDER/
# OUTPUT_DIR below rather than upstream's host-side /tmp scratch directory.
# The template wording itself is unchanged, and must stay that way -- see the
# module docstring.
# See ../../THIRD-PARTY-NOTICES.md for the full license text.
"""The prompt handed to the agent under test.

This wrapper is reproduced verbatim from the JobBench reference runners, where
it is byte-identical across all three (Claude Code, Codex CLI, OpenCode) and
varies only in the three interpolated paths. It is the benchmark's actual task
contract: it tells the agent where its inputs are, where deliverables must be
written, and that it may search the web but must not roam the filesystem.

Do not edit the wording. Scores produced under a different prompt are not
comparable to any other run of this benchmark, published or local. The only
thing that legitimately varies is the three paths, which differ here because
trials run inside a container rather than in a /tmp scratch directory on the
host.

Upstream: eval/run_benchmark_codex_cli.sh (and its two siblings), the
`prompt_msg` assignment.
"""

from __future__ import annotations

# Container-side layout. The agent may see everything under WORKSPACE and
# nothing else: rubrics, task cards, and any search-discoverable reference
# material stay on the host.
WORKSPACE = "/workspace"
TASK_FOLDER = f"{WORKSPACE}/task_folder"
OUTPUT_DIR = f"{WORKSPACE}/output"
PROMPT_PATH = f"{WORKSPACE}/prompt.txt"

_TEMPLATE = """=== TASK FOLDER ===
{task_folder}

=== INSTRUCTIONS ===
1. Read the TASK_INSTRUCTIONS.txt file in the task folder above
2. Based on the Reference Files section in TASK_INSTRUCTIONS.txt, read the corresponding files from the same task folder using appropriate tools.
3. Complete the task as specified in TASK_INSTRUCTIONS.txt
4. Only save the final deliverables to the output directory specified below. Do not save any intermediate or temporary files.

=== OUTPUT DIRECTORY ===
{output_dir}

IMPORTANT:
- All reference files are in the task folder: {task_folder}
- Only save the final deliverables to the output directory {output_dir}. Do not save any intermediate or temporary files.
- You MUST only access files within {workspace} or search online for new reference files if you find needed. Do NOT access any files or directories in this system outside of this path.
- If you encounter ambiguous or conflicting information, analyze the conflict, explain your reasoning, and justify the approach you choose.
- If a file cannot be read directly (e.g., .xlsx, .docx, .db, .pptx), use appropriate tools, MCP servers, or code to extract and process its contents."""


def render(
    *,
    workspace: str = WORKSPACE,
    task_folder: str = TASK_FOLDER,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """The exact bytes sent to the agent.

    Note what is absent: the task's own TASK_INSTRUCTIONS.txt is NOT inlined.
    Upstream points the agent at the file and expects it to read it, so the
    agent's ability to navigate its own workspace is part of what is measured.
    """
    return _TEMPLATE.format(
        workspace=workspace,
        task_folder=task_folder,
        output_dir=output_dir,
    )
