"""Chat-completions projection of the Amplifier Agent Python binding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._settings import Settings

if TYPE_CHECKING:
    from amplifier_agent import AgentOptions
    from starlette.applications import Starlette


def create_app(settings: Settings | None = None, options: AgentOptions | None = None) -> Starlette:
    from ._app import create_app as build_app

    return build_app(settings, options)


__all__ = ["Settings", "create_app"]
