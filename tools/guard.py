"""Path safety guards."""

from pathlib import Path


class PathGuard:
    def __init__(self, project_root: Path, allowed_paths: list[Path] | None = None):
        self.project_root = project_root.resolve()
        self.allowed = [p.resolve() for p in (allowed_paths or [])]

    def is_safe(self, target: Path) -> bool:
        resolved = target.resolve()
        if resolved == self.project_root or self.project_root in resolved.parents:
            return True
        for allowed in self.allowed:
            if resolved == allowed or allowed in resolved.parents:
                return True
        return False

    def assert_safe(self, target: Path) -> None:
        if not self.is_safe(target):
            raise ValueError(f"Path {target} is outside the allowed project tree.")
