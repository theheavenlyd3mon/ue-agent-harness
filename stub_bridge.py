"""Stub bridge server for local macOS testing."""

import json
import sys
import time
from pathlib import Path


def run(bridge_file: str, poll: float = 0.5):
    path = Path(bridge_file)
    print(f"[stub bridge] watching {path.resolve()}")
    while True:
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, FileNotFoundError):
                time.sleep(poll)
                continue

            if data.get("state") == "pending" and data.get("direction") == "agent":
                method = data.get("method", "unknown")
                params = data.get("params", {})

                # Simulate a simple UE response
                result: dict
                if method == "editor_command":
                    result = {"executed": params.get("command", "")}
                elif method == "compile_blueprints":
                    result = {"compiled": True, "method": method}
                elif method == "is_editor_running":
                    result = {"running": True, "mode": "stub"}
                elif method == "find_class":
                    result = {"found": False, "class_name": params.get("class_name", "")}
                elif method == "create_asset":
                    result = {"created": True, "asset_path": params.get("asset_path", "")}
                else:
                    result = {"acknowledged": True, "method": method}

                data["state"] = "done"
                data["direction"] = "editor"
                data["result"] = result
                data["error"] = None
                path.write_text(json.dumps(data, indent=2))
                print(f"[stub bridge] handled {method}")

        time.sleep(poll)


if __name__ == "__main__":
    bridge_file = sys.argv[1] if len(sys.argv) > 1 else "./bridge/bridge.json"
    run(bridge_file)
