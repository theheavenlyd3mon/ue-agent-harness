"""File-based bridge between agent and Unreal Editor."""

import json
import time
import uuid
from pathlib import Path
from typing import Any


class Bridge:
    def __init__(self, bridge_type: str, file_path: str, poll_interval: float, timeout: float):
        self.bridge_type = bridge_type
        self.file_path = Path(file_path)
        self.poll_interval = poll_interval
        self.timeout = timeout

        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def request(self, method: str, params: dict | None = None) -> dict:
        """Send a request to the editor and return the response."""
        if self.bridge_type == "stub":
            return self._stub_response(method, params)
        return self._file_request(method, params)

    def _file_request(self, method: str, params: dict | None = None) -> dict:
        request_id = uuid.uuid4().hex
        payload = {
            "state": "pending",
            "direction": "agent",
            "request_id": request_id,
            "method": method,
            "params": params or {},
            "result": None,
            "error": None,
        }
        self.file_path.write_text(json.dumps(payload, indent=2))

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                data = json.loads(self.file_path.read_text())
            except (json.JSONDecodeError, FileNotFoundError):
                time.sleep(self.poll_interval)
                continue
            if data.get("state") == "done" and data.get("request_id") == request_id:
                self.file_path.unlink(missing_ok=True)
                if data.get("error"):
                    return {"error": data["error"]}
                return data.get("result", {})
            time.sleep(self.poll_interval)

        return {"error": "Bridge timeout waiting for editor."}

    def _stub_response(self, method: str, params: dict | None = None) -> dict:
        if method == "editor_command":
            return {"executed": params.get("command", "")}
        if method == "compile_blueprints":
            return {"compiled": True}
        if method == "is_editor_running":
            return {"running": True, "mode": "stub"}
        return {"error": f"Unknown bridge method: {method}"}
