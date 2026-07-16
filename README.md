# AgentUnreal — UE Agent Harness

A standalone ReAct agent that drives Unreal Engine C++ changes through a **file-based bridge** to the editor. Runs on macOS in `stub` mode for local testing, then on Windows against the live Unreal Editor.

## Project layout

```
ue-agent-harness/
├── config.yaml               # Project + bridge + LLM settings
├── agent.py                  # Main ReAct loop
├── llm.py                    # Pluggable LLM client
├── repl.py                   # Interactive preview REPL
├── tui.py                    # Rich/Textual dashboard
├── stub_bridge.py            # macOS test bridge server
├── ue_bridge_listener.py     # Python script to run inside Unreal Editor
├── prompts/system.txt        # UE-aware system prompt
├── tools/
│   ├── bridge.py             # File bridge protocol
│   ├── file.py               # Read/write/dry-run utilities
│   ├── project.py            # Project scan / Build.cs read
│   ├── build.py              # UBT wrapper
│   └── editor.py             # Editor commands via bridge
├── test_stub.py              # Self-tests for stub mode
├── README.md                 # This file
└── .venv/                    # Python virtual environment
```

## Quick start (macOS, no UE editor)

```bash
cd /Users/noctis/Desktop/ue-agent-harness
source .venv/bin/activate
python3 test_stub.py          # verify stub mode
python3 repl.py               # interactive preview
python3 tui.py                # dashboard with task log, memory, journal
```

The REPL and TUI will attempt to call the LLM provider configured in `config.yaml`. If no API key is present, LLM calls will fail. To test the non-LLM tool chain, run `test_stub.py`.

## Configuration

Edit `config.yaml`:

```yaml
project:
  uproject_path: "C:/Projects/MurimSouls/MurimSouls.uproject"
  default_module: "MurimSouls"

bridge:
  type: "stub"                 # stub for local testing, file for real editor
  file_path: "./bridge/bridge.json"
  poll_interval: 0.5
  timeout: 30.0

llm:
  provider: "anthropic"        # anthropic | openai | openrouter | ollama | lmstudio
  model: "claude-sonnet-4"
  api_key_env: "ANTHROPIC_API_KEY"
  base_url: ""

agent:
  max_build_retries: 3
  memory_enabled: true
  journal_path: "./progress.md"
  db_path: "./memory.db"
```

## Windows setup (live UE editor)

1. Copy `ue_bridge_listener.py` into your Unreal project content or a Python plugin.
2. Run it inside the editor via **Execute Python Script**.
3. Set `bridge.type: "file"` in `config.yaml`.
4. Point `bridge.file_path` to the same path used by the listener (e.g. `C:\Projects\MurimSouls\bridge\bridge.json`).
5. Run `python3 agent.py` from the Windows machine.

## File bridge protocol

The bridge file is a single JSON document with a handshake state machine:

| State     | Direction | Meaning                        |
|-----------|-----------|--------------------------------|
| `pending` | `agent`   | Agent has written a request    |
| `done`    | `editor`  | Editor has written the response|

Example request:
```json
{
  "state": "pending",
  "direction": "agent",
  "request_id": "...",
  "method": "editor_command",
  "params": {"command": "stat fps"},
  "result": null,
  "error": null
}
```

Example response:
```json
{
  "state": "done",
  "direction": "editor",
  "request_id": "...",
  "method": "editor_command",
  "params": {"command": "stat fps"},
  "result": {"executed": "stat fps"},
  "error": null
}
```

## Dry-run workflow

The system prompt requires the agent to:
1. Call `dry_run` to preview the diff of any proposed file change.
2. Call `write_file` only after the dry-run result is returned.
3. Pair new `.h` files with `.cpp` files under the same module.
4. Call `build_module` after writing code.

## Testing

```bash
source .venv/bin/activate
python3 test_stub.py
```

This validates the stub bridge, missing-project scan, dry-run preview, and memory integration without making any real LLM calls.

## Roadmap

- [ ] Implement proper file bridge with `ue_bridge_listener.py` on Windows
- [x] Add memory/journal persistence
- [x] Add provider fallback (OpenAI / OpenRouter / local models)
- [x] Add CLI/TUI entry points
- [ ] Add Blueprint asset creation via bridge
- [ ] Add build error retry loop
- [ ] Add dry-run write guard / approval hook
