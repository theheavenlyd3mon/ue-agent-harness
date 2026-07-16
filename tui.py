"""AgentUnreal TUI — terminal dashboard for the UE agent harness.

Fixes the black-screen bug: agent.run() now runs in a Textual worker thread
so the UI keeps rendering. Agent.on_event is marshaled to the main thread and
streamed into the #log widget as the plan + each tool call happen.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, RichLog, Static, TabbedContent, TabPane, TextArea

from agent import Agent, Config


class AgentUnrealTUI(App):
    """A minimal dashboard for AgentUnreal."""

    CSS = """
    /* ponytail: dark noir/murim palette — desaturated, cold, faint blood accent */
    $primary: #6b3a3a;
    $background: #0c0c0e;
    $surface: #141417;
    $panel: #18181c;
    $text: #c8c8cf;
    $text-muted: #6a6a72;
    $accent: #9a4a4a;

    App { background: $background; color: $text; }
    #sidebar { width: 30%; border-right: solid $primary; background: $surface; }
    #main { width: 70%; background: $background; }
    #task-input { height: 3; border: solid $primary; }
    #status { height: 3; color: $text-muted; }
    #log { height: 1fr; border: solid $primary; background: $panel; }
    #memory-pane { height: 1fr; background: $panel; }
    #journal-pane { height: 1fr; background: $panel; }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+r", "run_task", "Run"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.agent = Agent(config)
        self._busy = False  # ponytail: guard against overlapping runs; thread worker not re-entrant here

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static("AgentUnreal", id="title")
                yield Button("Run Task", variant="primary", id="run-btn")
                yield Button("Clear Log", id="clear-btn")
                yield Static(
                    f"Bridge: {self.config.bridge_type}\n"
                    f"Memory: {'on' if self.config.memory_enabled else 'off'}\n"
                    f"Model: {self.config.llm_provider}/{self.config.llm_model}",
                    id="config-info",
                )
                yield Static("Ready", id="status")
            with Vertical(id="main"):
                yield Input(placeholder="Type a task (e.g. 'Add a stamina attribute')", id="task-input")
                with TabbedContent():
                    with TabPane("Task Log", id="log-tab"):
                        yield RichLog(id="log", markup=True, highlight=False, wrap=True)
                    with TabPane("Memory", id="memory-tab"):
                        yield TextArea(id="memory-pane", read_only=True, text="Memory is empty.")
                    with TabPane("Journal", id="journal-tab"):
                        yield TextArea(id="journal-pane", read_only=True, text="No journal entries.")
        yield Footer()

    def on_mount(self):
        self.log_widget().write(Text("AgentUnreal TUI started.", style="bold $primary"))
        self.log_widget().write(Text(f"Config: bridge={self.config.bridge_type}, memory={self.config.memory_enabled}", style="dim"))
        self._refresh_memory()

    def log_widget(self) -> RichLog:
        return self.query_one("#log", RichLog)

    def _log(self, text: Text) -> None:
        self.log_widget().write(text)

    @on(Button.Pressed, "#run-btn")
    def action_run_task(self):
        self._run_current_task()

    @on(Input.Submitted, "#task-input")
    def handle_input(self, event: Input.Submitted):
        self._run_current_task()

    def _run_current_task(self):
        if self._busy:
            return
        input_widget = self.query_one("#task-input", Input)
        prompt = input_widget.value.strip()
        if not prompt:
            return

        self._busy = True
        self.query_one("#status", Static).update(f"Running: {prompt[:60]}")
        self._log(Text("▶ ", style="cyan") + Text(prompt, style="bold"))
        input_widget.value = ""
        self.agent.on_event = self._agent_event  # ponytail: hook into the run loop
        # ponytail: thread=True runs the sync fn off the main loop; run_worker's
        # generic type wants Awaitable but Textual wraps sync fns for thread mode — known false positive.
        self.run_worker(self._execute, prompt, thread=True, group="task")  # type: ignore[arg-type]

    def _execute(self, prompt: str) -> None:
        try:
            result = self.agent.run(prompt)
        except Exception as e:  # surface the failure into the log, don't swallow
            result = f"Error: {e}"
        self.call_from_thread(self._finalize, result)  # ponytail: marshal back to main thread

    def _finalize(self, result: str) -> None:
        self._log(Text("◆ ", style="magenta") + Text(result[:600], style="bold"))
        self.query_one("#status", Static).update("Ready")
        self._busy = False
        self.agent.on_event = None
        self._refresh_memory()
        self._refresh_journal()

    def _agent_event(self, event_type: str, **kw: object) -> None:
        # Fired from the worker thread — marshal to main thread for widget writes.
        self.call_from_thread(self._render_event, event_type, kw)

    def _render_event(self, event_type: str, kw: dict) -> None:
        if event_type == "plan":
            steps = kw.get("steps") or []
            self._log(Text("▸ Plan", style="bold cyan"))
            for i, s in enumerate(steps):
                self._log(Text(f"  {i+1}. {s.get('tool','?')} — {s.get('reason','')}", style="dim"))
        elif event_type == "tool.start":
            name = kw.get("name", "?")
            self._log(Text("⏳ ", style="yellow") + Text(str(name), style="bold") + Text("  running…", style="dim"))
        elif event_type == "tool.complete":
            name = kw.get("name", "?")
            res = kw.get("result", {})
            if isinstance(res, dict) and "error" in res:
                self._log(Text("✗ ", style="red") + Text(str(name), style="bold") + Text(f"  {res['error'][:160]}", style="dim"))
            else:
                summary = _summarize(res)
                self._log(Text("✓ ", style="green") + Text(str(name), style="bold") + Text(f"  {summary}", style="dim"))
        # 'result' handled in _finalize

    @on(Button.Pressed, "#clear-btn")
    def clear_log(self):
        self.log_widget().clear()

    def _refresh_memory(self):
        if not self.agent.memory:
            return
        try:
            stats = self.agent.memory.get_stats()
            memories = self.agent.memory.get_context(limit=10)
            lines = [f"Total memories: {stats.get('total_memories', 0)}", ""]
            for m in memories:
                lines.append(f"- {m.get('content', '')[:200]}")
            self.query_one("#memory-pane", TextArea).text = "\n".join(lines)
        except Exception as e:
            self.query_one("#memory-pane", TextArea).text = f"Memory error: {e}"

    def _refresh_journal(self):
        path = Path(self.config.journal_path)
        if path.exists():
            self.query_one("#journal-pane", TextArea).text = path.read_text()
        else:
            self.query_one("#journal-pane", TextArea).text = "No journal yet."


def _summarize(res: object, limit: int = 140) -> str:
    import json
    try:
        txt = json.dumps(res, ensure_ascii=False)
    except Exception:
        txt = str(res)
    return (txt[:limit] + "…") if len(txt) > limit else txt


def main():
    parser = argparse.ArgumentParser(description="AgentUnreal TUI")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    config = Config.from_yaml(args.config)
    app = AgentUnrealTUI(config)
    app.run()


if __name__ == "__main__":
    main()
