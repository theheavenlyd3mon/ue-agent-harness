"""File system tools."""

import difflib
from pathlib import Path
from typing import Any


class FileTools:
    def __init__(self, guard: Any = None):
        self.guard = guard

    def _check(self, path: Path) -> dict | None:
        if self.guard:
            try:
                self.guard.assert_safe(path)
            except ValueError as e:
                return {"error": str(e)}
        return None

    def read_file(self, path: str) -> dict:
        """Read a file from disk."""
        p = Path(path)
        guard_error = self._check(p)
        if guard_error:
            return guard_error
        if not p.exists():
            return {"error": f"File not found: {path}"}
        return {"path": str(p), "content": p.read_text()}

    def write_file(self, path: str, content: str) -> dict:
        """Write content to a file, creating parent directories if needed."""
        p = Path(path)
        guard_error = self._check(p)
        if guard_error:
            return guard_error
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return {"path": str(p), "bytes_written": len(content)}

    def list_source_files(self, module_path: str) -> dict:
        """List .h and .cpp files under a module path."""
        p = Path(module_path)
        guard_error = self._check(p)
        if guard_error:
            return guard_error
        if not p.exists():
            return {"error": f"Module path not found: {module_path}"}
        headers = list(p.rglob("*.h"))
        sources = list(p.rglob("*.cpp"))
        return {
            "headers": [str(h) for h in headers],
            "sources": [str(c) for c in sources],
        }

    def dry_run(self, path: str, content: str) -> dict:
        """Show the diff of a proposed file change without writing it."""
        p = Path(path)
        guard_error = self._check(p)
        if guard_error:
            return guard_error
        existing = p.read_text() if p.exists() else ""
        diff = list(difflib.unified_diff(
            existing.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=str(path) + (" (existing)" if p.exists() else " (new)"),
            tofile=str(path),
        ))
        return {
            "path": str(p),
            "would_create": not p.exists(),
            "diff": "".join(diff),
        }
