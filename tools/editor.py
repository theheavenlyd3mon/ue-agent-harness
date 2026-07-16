"""Editor tools that communicate through the bridge."""

from typing import Any

from tools.bridge import Bridge


class EditorTools:
    def __init__(self, bridge: Bridge):
        self.bridge = bridge

    def editor_command(self, command: str) -> dict:
        return self.bridge.request("editor_command", {"command": command})

    def compile_blueprints(self) -> dict:
        return self.bridge.request("compile_blueprints")

    def is_editor_running(self) -> dict:
        return self.bridge.request("is_editor_running")
