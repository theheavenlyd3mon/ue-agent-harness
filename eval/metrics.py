"""Session metrics tracking."""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionMetrics:
    session_id: str
    task: str
    iterations: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    build_attempts: int = 0
    build_success: bool = False
    final_status: str = "incomplete"
    errors: list[str] = field(default_factory=list)

    def record_tool(self, name: str) -> None:
        self.tool_calls[name] = self.tool_calls.get(name, 0) + 1

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "task": self.task,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "build_attempts": self.build_attempts,
            "build_success": self.build_success,
            "final_status": self.final_status,
            "errors": self.errors,
        }

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(self.to_dict()) + "\n")
