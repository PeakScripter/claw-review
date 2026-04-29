"""Slash command registry — mirrors Claude Code's commands.ts pattern.

Commands are local (no LLM call) and execute synchronously against the current
review session state.  Each command returns a string to display in the REPL.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Command:
    name: str
    description: str
    usage: str
    handler: Callable[..., str]


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self._commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def all(self) -> list[Command]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def dispatch(self, raw: str, session: Any) -> str | None:
        """Parse and execute a slash command.  Returns output text or None if not a command."""
        if not raw.startswith("/"):
            return None
        parts = raw[1:].split(None, 1)
        name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        cmd = self._commands.get(name)
        if cmd is None:
            return f"Unknown command: /{name}\nType /help for available commands."
        try:
            return cmd.handler(args=args, session=session)
        except Exception as e:
            return f"/{name} error: {e}"
