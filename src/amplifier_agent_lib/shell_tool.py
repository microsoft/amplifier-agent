"""Platform selection for the shell tool.

``bundle.md`` declares BOTH shell modules. Exactly one survives into any given
session's mount plan, and this module is what picks it:

    Windows   ->  tool-pwsh    (PowerShell, from amplifier-bundle-windows-shell)
    elsewhere ->  tool-bash

Why declare both and filter, rather than declare one conditionally: the bundle
manifest is static and its sha256 IS the prepared-cache key, so there is no
conditional syntax to hang this on. Declaring both also means
``bundle.prepare(install_deps=True)`` installs both, which costs one extra clone
of a zero-dependency package on POSIX and buys a mount plan that cannot be
missing its shell tool on either platform.

Why swap rather than mount both: on Windows ``tool-bash`` does not find a shell.
Git for Windows ships ``bash.exe`` in a directory its installer leaves off PATH,
so ``shutil.which("bash")`` returns None and commands fall through to a branch
that tries to exec ``pwd`` as a binary. The model then sees an unactionable
``[WinError 2]`` and concludes the tool is broken. Leaving both mounted would
keep that trap one wrong choice away, and would spend context on two shell tool
descriptions to do it.

The name difference is deliberate upstream and is load-bearing here. The tool is
``pwsh``, not ``bash`` backed by PowerShell, because the tool name is a strong
prior on the SYNTAX the model emits. Anything that refers to the shell tool by
literal name therefore has to name both; the shipped modes do (see
``bundle/modes/*.md``).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["POSIX_SHELL_MODULE", "WINDOWS_SHELL_MODULE", "select_shell_tool"]

WINDOWS_SHELL_MODULE = "tool-pwsh"
POSIX_SHELL_MODULE = "tool-bash"


def select_shell_tool(mount_plan: dict[str, Any], *, is_windows: bool | None = None) -> str | None:
    """Drop the shell module that does not belong on this platform, in place.

    Args:
        mount_plan: A mount plan dict. ``mount_plan["tools"]`` is a list of
            ``{module, config, source}`` entries, the shape
            ``Bundle.to_mount_plan()`` produces.
        is_windows: Platform override, for tests. Defaults to the real platform.

    Returns:
        The module id that was dropped, or ``None`` if nothing was dropped
        (either it was not declared, or the plan has no tools).

    Tolerates a manifest that declares neither module, or only the one being
    kept. A missing entry is not an error here: this function's job is to
    remove, and an operator who has already narrowed the roster has made a
    choice worth respecting rather than second-guessing at mount time.
    """
    if is_windows is None:
        is_windows = sys.platform == "win32"

    drop = POSIX_SHELL_MODULE if is_windows else WINDOWS_SHELL_MODULE
    tools = mount_plan.get("tools")
    if not isinstance(tools, list):
        return None

    remaining = [entry for entry in tools if entry.get("module") != drop]
    if len(remaining) == len(tools):
        return None

    # Mutate the SAME list object. Callers hold references to
    # ``prepared.mount_plan["tools"]`` and rebinding the key would leave them
    # pointing at the unfiltered list.
    tools[:] = remaining
    kept = WINDOWS_SHELL_MODULE if is_windows else POSIX_SHELL_MODULE
    logger.info("shell tool: kept %s, dropped %s (platform=%s)", kept, drop, sys.platform)
    return drop
