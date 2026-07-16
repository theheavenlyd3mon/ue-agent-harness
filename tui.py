"""AgentUnreal TUI — two-pane terminal dashboard for the UE agent harness.

Layout:
  ┌─────────────────────┬───────────────────────────┐
  │  CHAT (scrollback)  │  TOOL CALLS (live cards)  │
  │  you › …            │  ⏳ scan_project          │
  │  agent › …          │  ✓ write_file            │
  ├─────────────────────┴───────────────────────────┤
  │  > task input                                   │
  └─────────────────────────────────────────────────┘

Fixes the black-screen bug: agent.run() runs in a worker thread so the UI
keeps rendering. Agent.on_event is marshaled to the main thread and streamed
into the chat + tool-call panes as the plan and each tool call happen.
"""
from __future__ import annotations

import argparse
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, RichLog, Static, TextArea

from agent import Agent, Config


class AgentUnrealTUI(App):
    """Two-pane dashboard: chat history + live tool-call cards."""

    CSS = """
    /* ponytail: dark noir/murim palette — desaturated, cold, faint blood accent */
    $primary: #6b3a3a;
    $background: #0c0c0e;
    $surface: #141417;
    $panel: #18181c;
    $text: #c8c8cf;
    $text-muted: #6a6a72;
    $accent: #9a4a4a;
    $you: #7a8a9a;
    $agent: #c8c8cf;

    App { background: $background; color: $text; }
    #top { height: 1fr; }
    #chat-pane { width: 55%; border-right: solid $primary; background: $surface; }
    #tools-pane { width: 45%; background: $panel; }
    #chat-log { height: 1fr; background: $surface; }
    #tools-log { height: 1fr; background: $panel; }
    #input-bar { height: 3; }
    #task-input { height: 3; border: solid $primary; width: 1fr; }
    #run-btn { width: 12; }
    #status { height: 1; color: $text-muted; }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+r", "run_task", "Run"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.agent = Agent(config)
        self._busy = False
        self._call_counter = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="top"):
            with VerticalScroll(id="chat-pane"):
                yield Static("CHAT", classes="pane-title")
                yield RichLog(id="chat-log", markup=True, highlight=False, wrap=True, max_lines=2000)
            with VerticalScroll(id="tools-pane"):
                yield Static("TOOL CALLS", classes="pane-title")
                yield RichLog(id="tools-log", markup=True, highlight=False, wrap=True, max_lines=2000)
        with Horizontal(id="input-bar"):
            yield Input(placeholder="Type a task (e.g. 'Add a stamina attribute')", id="task-input")
            yield Button("Run", variant="primary", id="run-btn")
        yield Static("Ready", id="status")
        yield Footer()

    def on_mount(self):
        self.chat().write(Text("AgentUnreal ready. Type a task below.", style="bold $primary"))
        self.chat().write(Text(f"bridge={self.config.bridge_type} · memory={'on' if self.config.memory_enabled else 'off'} · {self.config.llm_provider}/{self.config.llm_model}", style="dim"))
        self._refresh_memory()

    # widget accessors
    def chat(self) -> RichLog:
        return self.query_one("#chat-log", RichLog)

    def tools(self) -> RichLog:
        return self.query_one("#tools-log", RichLog)

    # chat helpers
    def _say_you(self, text: str) -> None:
        self.chat().write(Text("you › ", style="bold $you") + Text(text))

    def _say_agent(self, text: str) -> None:
        self.chat().write(Text("agent › ", style="bold $agent") + Text(text))

    def _plan_note(self, steps: list) -> None:
        self.chat().write(Text("plan › ", style="bold cyan") + Text(
            " · ".join(f"{s.get('tool','?')}" for s in steps), style="dim"))

    # actions
    @on(Button.Pressed, "#run-btn")
    def action_run_task(self):
        self._run_current_task()

    @on(Input.Submitted, "#task-input")
    def handle_input(self, _):
        self._run_current_task()

    def _run_current_task(self):
        if self._busy:
            return
        prompt = self.query_one("#task-input", Input).value.strip()
        if not prompt:
            return
        self._busy = True
        self._call_counter = 0
        self._say_you(prompt)
        self.query_one("#task-input", Input).value = ""
        self.query_one("#status", Static).update(f"Running: {prompt[:60]}")
        self.agent.on_event = self._agent_event
        self.run_worker(self._execute, prompt, thread=True, group="task")  # type: ignore[arg-type]

    def _execute(self, prompt: str) -> None:
        try:
            result = self.agent.run(prompt)
        except Exception as e:
            result = f"Error: {e}"
        self.call_from_thread(self._finalize, result)

    def _finalize(self, result: str) -> None:
        self._say_agent(result[:1200])
        self.query_one("#status", Static).update("Ready")
        self._busy = False
        self.agent.on_event = None

    # event marshaling (worker -> main thread)
    def _agent_event(self, event_type: str, **kw: object) -> None:
        self.call_from_thread(self._render_event, event_type, kw)

    def _render_event(self, event_type: str, kw: dict) -> None:
        if event_type == "plan":
            self._plan_note(kw.get("steps") or [])
        elif event_type == "tool.start":
            self._call_counter += 1
            name = kw.get("name", "?")
            self.tools().write(
                Text(f"⏳ [{self._call_counter}] ", style="yellow")
                + Text(str(name), style="bold")
                + Text(f"  args={_short(kw.get('args', {}))}", style="dim"))
        elif event_type == "tool.complete":
            name = kw.get("name", "?")
            res = kw.get("result", {})
            if isinstance(res, dict) and "error" in res:
                self.tools().write(
                    Text(f"✗ [{self._call_counter}] ", style="red")
                    + Text(str(name), style="bold")
                    + Text(f"  {res['error'][:200]}", style="dim"))
            else:
                self.tools().write(
                    Text(f"✓ [{self._call_counter}] ", style="green")
                    + Text(str(name), style="bold")
                    + Text(f"  {_short(res, 200)}", style="dim"))


def _short(obj: object, limit: int = 160) -> str:
    import json
    try:
        txt = json.dumps(obj, ensure_ascii=False)
    except Exception:
        txt = str(obj)
    return (txt[:limit] + "…") if len(txt) > limit else txt


def main():
    parser = argparse.ArgumentParser(description="AgentUnreal TUI")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()
    app = AgentUnrealTUI(Config.from_yaml(args.config))
    app.run()


if __name__ == "__main__":
    main()
