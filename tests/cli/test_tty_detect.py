"""Tests for amplifier_agent_cli.tty_detect — TTY detection helpers for stdin/stdout."""

from __future__ import annotations

from unittest.mock import patch

from amplifier_agent_cli.tty_detect import is_stdin_tty, is_stdout_tty

# ---------------------------------------------------------------------------
# is_stdin_tty tests
# ---------------------------------------------------------------------------


def test_is_stdin_tty_true_when_isatty_true() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", return_value=True) as mock_isatty:
        result = is_stdin_tty()
    assert result is True
    mock_isatty.assert_called_once_with(0)


def test_is_stdin_tty_false_when_isatty_false() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", return_value=False) as mock_isatty:
        result = is_stdin_tty()
    assert result is False
    mock_isatty.assert_called_once_with(0)


def test_is_stdin_tty_false_when_oserror() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", side_effect=OSError("bad fd")) as mock_isatty:
        result = is_stdin_tty()
    assert result is False
    mock_isatty.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# is_stdout_tty tests
# ---------------------------------------------------------------------------


def test_is_stdout_tty_true_when_isatty_true() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", return_value=True) as mock_isatty:
        result = is_stdout_tty()
    assert result is True
    mock_isatty.assert_called_once_with(1)


def test_is_stdout_tty_false_when_isatty_false() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", return_value=False) as mock_isatty:
        result = is_stdout_tty()
    assert result is False
    mock_isatty.assert_called_once_with(1)


def test_is_stdout_tty_false_when_oserror() -> None:
    with patch("amplifier_agent_cli.tty_detect.os.isatty", side_effect=OSError("bad fd")) as mock_isatty:
        result = is_stdout_tty()
    assert result is False
    mock_isatty.assert_called_once_with(1)
