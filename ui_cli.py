"""Minimal live CLI renderer — shows plan + tool calls as they happen.

Wired to Agent.on_event. Streams via rich.live.Live so the terminal is
never a black screen. Unicode marks: ▶ start, ⏳ running, ✓ ok, ✗ error.
"""
from __future__ import annotations

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # braille spinner (fork uses unicode-animations; this is stdlib-free)


class LiveRenderer:
    """One live view. call .on_event(type, **kw) from Agent."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self.live = Live(console=self.console, refresh_per_second=12, transient=False)
        self._spin = 0
        self._plan: list = []
        self._pending: dict[str, dict] = {}      # id -> {name, args}
        self._done: list[tuple[str, str, str]] = []  # (name, mark, summary)
        self._status = "idle"
        self._spin_tick = 0

    def __enter__(self) -> "LiveRenderer":
        self.live.__enter__()
        return self

    def __exit__(self, *_: object) -> None:
        self.live.__exit__(None, None, None)

    def on_event(self, event_type: str, **kw: object) -> None:
        if event_type == "plan":
            self._plan = kw.get("steps") or []  # type: ignore[assignment]
            self._status = "planning"
        elif event_type == "tool.start":
            self._pending[str(kw["id"])] = {"name": str(kw["name"]), "args": kw.get("args", {})}
            self._status = "running"
        elif event_type == "tool.complete":
            cid = str(kw["id"])
            name = str(kw["name"])
            res = kw.get("result", {})
            if isinstance(res, dict) and "error" in res:
                mark, summary = "✗", res["error"][:120]
            else:
                mark, summary = "✓", self._summarize(res)
            self._pending.pop(cid, None)
            self._done.append((name, mark, summary))
        elif event_type == "result":
            self._status = "done"
        self._render()

    def _summarize(self, res: object, limit: int = 120) -> str:
        txt = json_str(res)
        return (txt[:limit] + "…") if len(txt) > limit else txt

    def _render(self) -> None:
        self._spin_tick = (self._spin_tick + 1) % len(_SPINNER)
        spin = _SPINNER[self._spin_tick]
        blocks: list = []

        if self._plan:
            steps = "\n".join(f"  {i+1}. {s.get('tool','?')} — {s.get('reason','')}" for i, s in enumerate(self._plan))
            blocks.append(Panel(steps, title="▸ Plan", border_style="cyan", expand=False))

        for cid, p in self._pending.items():
            args_s = self._summarize(p["args"], 80)
            blocks.append(Panel(f"[dim]{args_s}[/dim]", title=f"{spin} {p['name']}  [yellow]running…[/yellow]", border_style="yellow", expand=False))

        for name, mark, summary in self._done:
            style = "green" if mark == "✓" else "red"
            blocks.append(Panel(f"[dim]{summary}[/dim]", title=f"{mark} {name}", border_style=style, expand=False))

        header = Text(f"status: {self._status}", style="bold")
        self.live.update(Group(header, *blocks) if blocks else header)


def json_str(obj: object) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)
