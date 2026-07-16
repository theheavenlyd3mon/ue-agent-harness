"""AgentUnreal TUI — terminal dashboard for the UE agent harness."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from rich.markdown import Markdown
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Log, Static, TabbedContent, TabPane, TextArea

from agent import Agent, Config


class AgentUnrealTUI(App):
    """A minimal dashboard for AgentUnreal."""

    CSS = """
    #sidebar { width: 30%; border-right: solid $primary; }
    #main { width: 70%; }
    #task-input { height: 3; }
    #status { height: 3; color: $text-muted; }
    #log { height: 1fr; border: solid $primary; }
    #memory-pane { height: 1fr; }
    #journal-pane { height: 1fr; }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+r", "run_task", "Run"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.agent = Agent(config)
        self.session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self.session_path = Path("sessions") / f"{self.session_id}.jsonl"
        self.session_path.parent.mkdir(parents=True, exist_ok=True)

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
                        yield Log(id="log")
                    with TabPane("Memory", id="memory-tab"):
                        yield TextArea(id="memory-pane", read_only=True, text="Memory is empty.")
                    with TabPane("Journal", id="journal-tab"):
                        yield TextArea(id="journal-pane", read_only=True, text="No journal entries.")
        yield Footer()

    def on_mount(self):
        self.log("AgentUnreal TUI started.")
        self.log(f"Session: {self.session_id}")
        self.log(f"Config: bridge={self.config.bridge_type}, memory={self.config.memory_enabled}")
        self._refresh_memory()

    def log(self, message: str):
        log_widget = self.query_one("#log", Log)
        timestamp = datetime.utcnow().strftime("%H:%M:%S")
        log_widget.write_line(f"[{timestamp}] {message}")
        self._append_session_log(message)

    def _append_session_log(self, message: str):
        with self.session_path.open("a") as f:
            f.write(json.dumps({"timestamp": datetime.utcnow().isoformat(), "message": message}) + "\n")

    @on(Button.Pressed, "#run-btn")
    def action_run_task(self):
        self._run_current_task()

    @on(Input.Submitted, "#task-input")
    def handle_input(self, event: Input.Submitted):
        self._run_current_task()

    def _run_current_task(self):
        input_widget = self.query_one("#task-input", Input)
        prompt = input_widget.value.strip()
        if not prompt:
            return

        self.query_one("#status", Static).update(f"Running: {prompt}")
        self.log(f"User: {prompt}")
        input_widget.value = ""

        try:
            result = self.agent.run(prompt)
        except Exception as e:
            result = f"Error: {e}"

        self.log(f"Agent: {result}")
        self.query_one("#status", Static).update("Ready")
        self._refresh_memory()
        self._refresh_journal()

    @on(Button.Pressed, "#clear-btn")
    def clear_log(self):
        self.query_one("#log", Log).clear()

    def _refresh_memory(self):
        if not self.agent.memory:
            return
        try:
            stats = self.agent.memory.get_stats()
            memories = self.agent.memory.get_context(limit=10)
            lines = [f"Total memories: {stats.get('total_memories', 0)}", ""]
            for m in memories:
                content = m.get("content", "")
                lines.append(f"- {content[:200]}")
            self.query_one("#memory-pane", TextArea).text = "\n".join(lines)
        except Exception as e:
            self.query_one("#memory-pane", TextArea).text = f"Memory error: {e}"

    def _refresh_journal(self):
        path = Path(self.config.journal_path)
        if path.exists():
            self.query_one("#journal-pane", TextArea).text = path.read_text()
        else:
            self.query_one("#journal-pane", TextArea).text = "No journal yet."


def main():
    parser = argparse.ArgumentParser(description="AgentUnreal TUI")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    config = Config.from_yaml(args.config)
    app = AgentUnrealTUI(config)
    app.run()


if __name__ == "__main__":
    main()
