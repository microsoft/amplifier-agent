"""Static invariant check: amplifier_agent_lib must not call print( or reference sys.stdout.

All output must flow through injected ProtocolPoints (DisplaySystem). This file
enforces that invariant by scanning executable source (docstrings/comments excluded)
via tokenize + ast.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import amplifier_agent_lib

PKG_ROOT = Path(amplifier_agent_lib.__file__).parent


def _executable_source(path: Path) -> str:
    """Return the executable source of *path* with docstrings and comments stripped.

    Strategy:
    1. Parse AST to identify docstring line ranges (first body element of
       Module/FunctionDef/AsyncFunctionDef/ClassDef that is an Expr with a
       Constant string value).
    2. Tokenize and drop COMMENT tokens and STRING tokens whose start line
       falls within a docstring range.
    3. Reconstruct via tokenize.untokenize (preserves original column positions
       so patterns like ``print(`` and ``sys.stdout`` survive intact).
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Collect line numbers that are part of docstrings.
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ds = body[0]
                end_lineno = ds.end_lineno if ds.end_lineno is not None else ds.lineno
                for lineno in range(ds.lineno, end_lineno + 1):
                    docstring_lines.add(lineno)

    # Tokenize and filter out comments and docstring string literals.
    filtered: list[tokenize.TokenInfo] = []
    readline = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.start[0] in docstring_lines:
            continue
        filtered.append(tok)

    return tokenize.untokenize(filtered)


def _iter_py_files() -> list[Path]:
    """Return a sorted list of all .py files under PKG_ROOT."""
    return sorted(PKG_ROOT.rglob("*.py"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_print_in_lib_sources() -> None:
    """Executable source in amplifier_agent_lib must not call print(."""
    violations: list[str] = []
    for path in _iter_py_files():
        source = _executable_source(path)
        if "print(" in source:
            violations.append(str(path.relative_to(PKG_ROOT)))
    assert not violations, (
        "Found 'print(' in executable code in the following files:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nRoute the offending write through self._points['display'] or a "
        "callable provided to the offender's constructor (see engine.py and "
        "defaults_cli.py for the established pattern)."
    )


def test_no_sys_stdout_in_lib_sources() -> None:
    """Executable source in amplifier_agent_lib must not reference sys.stdout."""
    violations: list[str] = []
    for path in _iter_py_files():
        source = _executable_source(path)
        if "sys.stdout" in source:
            violations.append(str(path.relative_to(PKG_ROOT)))
    assert not violations, (
        "Found 'sys.stdout' in executable code in the following files:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nRoute the offending write through self._points['display'] or a "
        "callable provided to the offender's constructor (see engine.py and "
        "defaults_cli.py for the established pattern)."
    )


def test_library_files_scanned_nonempty() -> None:
    """Sanity check: at least 5 Python files are scanned under PKG_ROOT."""
    files = _iter_py_files()
    assert len(files) >= 5, f"Expected >= 5 Python files under {PKG_ROOT}, found {len(files)}: {files}"
