"""HTTP host settings, resolved once when the service starts."""

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    token: str
    bind: str = "127.0.0.1"
    port: int = 9099
    model: str = "amplifier"

    def __post_init__(self) -> None:
        if not self.token or not self.token.strip():
            raise ValueError("Set AMPLIFIER_AGENT_FACE_TOKEN to a nonempty bearer token.")
        if not self.bind or not self.model:
            raise ValueError("Set a nonempty face bind address and model name.")
        if isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise ValueError("Set AMPLIFIER_AGENT_FACE_PORT to an integer from 1 to 65535.")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environment is None else environment
        try:
            port = int(env.get("AMPLIFIER_AGENT_FACE_PORT", "9099"))
        except ValueError:
            raise ValueError(
                "Set AMPLIFIER_AGENT_FACE_PORT to an integer from 1 to 65535."
            ) from None
        return cls(
            token=env.get("AMPLIFIER_AGENT_FACE_TOKEN", ""),
            bind=env.get("AMPLIFIER_AGENT_FACE_BIND", "127.0.0.1"),
            port=port,
            model=env.get("AMPLIFIER_AGENT_FACE_MODEL", "amplifier"),
        )
