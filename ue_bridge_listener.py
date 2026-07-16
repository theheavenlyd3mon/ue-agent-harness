"""UE editor-side bridge listener. Run inside Unreal Editor via Execute Python Script."""

import json
import time
from pathlib import Path

import unreal


BRIDGE_FILE = Path(r"C:\Projects\MurimSouls\bridge\bridge.json")
POLL_INTERVAL = 0.5


def handle_editor_command(params: dict) -> dict:
    command = params.get("command", "")
    if command:
        unreal.SystemLibrary.execute_console_command(unreal.EditorUtilityLibrary.get_editor_world(), command)
    return {"executed": command}


def handle_compile_blueprints(params: dict) -> dict:
    unreal.EditorAssetLibrary.compile_blueprints()
    return {"compiled": True}


def handle_is_editor_running(params: dict) -> dict:
    return {"running": True, "world": str(unreal.EditorUtilityLibrary.get_editor_world())}


HANDLERS = {
    "editor_command": handle_editor_command,
    "compile_blueprints": handle_compile_blueprints,
    "is_editor_running": handle_is_editor_running,
}


def process_request(data: dict) -> dict:
    method = data.get("method")
    params = data.get("params", {})
    handler = HANDLERS.get(method)
    if not handler:
        return {"error": f"Unknown method: {method}"}
    try:
        return {"result": handler(params)}
    except Exception as e:
        return {"error": str(e)}


def main():
    while True:
        if not BRIDGE_FILE.exists():
            time.sleep(POLL_INTERVAL)
            continue

        try:
            data = json.loads(BRIDGE_FILE.read_text())
        except Exception:
            time.sleep(POLL_INTERVAL)
            continue

        if data.get("state") != "pending" or data.get("direction") != "agent":
            time.sleep(POLL_INTERVAL)
            continue

        data["state"] = "processing"
        BRIDGE_FILE.write_text(json.dumps(data, indent=2))

        result = process_request(data)
        data["state"] = "done"
        data.update(result)
        BRIDGE_FILE.write_text(json.dumps(data, indent=2))


try:
    main()
except KeyboardInterrupt:
    pass
except Exception as e:
    unreal.log_error(f"UE bridge listener error: {e}")
