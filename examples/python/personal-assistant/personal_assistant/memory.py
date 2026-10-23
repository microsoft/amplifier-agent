"""The home directory: plain files the user can read and edit, and the only memory the assistant keeps.

MEMORY.md holds lasting facts as bullets, memory/YYYY-MM-DD.md logs each day's conversation, HEARTBEAT.md is a
checklist the heartbeat reads, and reminders.json holds reminders that have not been delivered yet.
"""

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

OPEN_ITEM = "- [ ] "


@dataclass(frozen=True)
class Reminder:
    at: datetime
    text: str


@dataclass(frozen=True)
class Home:
    root: Path

    @property
    def memory(self) -> Path:
        return self.root / "MEMORY.md"

    @property
    def logs(self) -> Path:
        return self.root / "memory"

    @property
    def checklist(self) -> Path:
        return self.root / "HEARTBEAT.md"

    @property
    def reminders(self) -> Path:
        return self.root / "reminders.json"

    def read_memory(self) -> str:
        return self.memory.read_text(encoding="utf-8") if self.memory.exists() else ""

    def remember(self, fact: str) -> bool:
        """Append a fact as one bullet. False when the same bullet is already there."""
        bullet = "- " + " ".join(fact.split())
        text = self.read_memory()
        if bullet in (line.strip() for line in text.splitlines()):
            return False
        with self.memory.open("a", encoding="utf-8") as file:
            if text and not text.endswith("\n"):
                file.write("\n")
            file.write(bullet + "\n")
        return True

    def forget(self, match: str) -> list[str]:
        """Remove every bullet that contains `match`, ignoring case, and return the removed lines."""
        needle = match.strip().lower()
        if not needle:
            return []
        kept, removed = [], []
        for line in self.read_memory().splitlines():
            is_match = line.lstrip().startswith("- ") and needle in line.lower()
            (removed if is_match else kept).append(line)
        if removed:
            self.memory.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return removed

    def append_log(self, role: str, text: str, now: datetime | None = None) -> Path:
        now = now or datetime.now()
        path = self.logs / f"{now:%Y-%m-%d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            if file.tell() == 0:
                file.write(f"# {now:%Y-%m-%d}\n")
            file.write(f"\n## {now:%H:%M} {role}\n\n{text.strip()}\n")
        return path

    def open_items(self) -> list[str]:
        if not self.checklist.exists():
            return []
        lines = self.checklist.read_text(encoding="utf-8").splitlines()
        return [line.removeprefix(OPEN_ITEM).strip() for line in lines if line.startswith(OPEN_ITEM)]

    def load_reminders(self) -> list[Reminder]:
        if not self.reminders.exists():
            return []
        entries = json.loads(self.reminders.read_text(encoding="utf-8"))
        return [Reminder(datetime.fromisoformat(entry["at"]), entry["text"]) for entry in entries]

    def save_reminders(self, reminders: list[Reminder]) -> None:
        entries = [{"at": reminder.at.isoformat(timespec="minutes"), "text": reminder.text} for reminder in reminders]
        self.reminders.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")

    def add_reminder(self, at: datetime, text: str) -> Reminder:
        reminder = Reminder(at, text)
        self.save_reminders(sorted([*self.load_reminders(), reminder], key=lambda item: item.at))
        return reminder

    def due_reminders(self, now: datetime) -> list[Reminder]:
        return [reminder for reminder in self.load_reminders() if reminder.at <= now]

    def remove_reminders(self, delivered: list[Reminder]) -> None:
        self.save_reminders([reminder for reminder in self.load_reminders() if reminder not in delivered])
