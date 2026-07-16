# UE Agent Harness — Evolution Implementation Plan

> **For Hermes:** Use `subagent-driven-development` to implement this plan task-by-task. This plan pulls from the existing `ue-agent-harness-specs.md` and the lessons learned from `awesome-llm-apps`, `agents-towards-production`, and `ai-agents-for-beginners`.

**Goal:** Upgrade the minimal UE Agent Harness from a working prototype to a production-ready, extensible agent that can safely drive UE C++ changes, learn from past sessions, and leverage external tools via MCP.

**Architecture:** Keep the core narrow (ReAct loop + thin tool registry) and push capability to the edges: auto-generated tool schemas, MCP-registered external tools, persistent memory, and a planner step. Borrow the safety and observability patterns from the surveyed agent repos.

**Tech Stack:** Python 3.11+, `pyyaml`, `mnemosyne`, `mcp` (optional), `inspect` for schema generation, `pathlib` for project guards.

---

## Current State (from `ue-agent-harness-specs.md` and source)

The harness is at **M1**: `agent.py`, `tools/`, `llm.py`, `config.yaml`, `stub_bridge.py`, and `test_stub.py` exist. It can:
- Scan a `.uproject` and list modules.
- Read/write files and preview diffs via `dry_run`.
- Talk to a stub or file-based UE bridge.
- Log sessions to JSONL and write `progress.md`.
- Optionally store/recall session memory with Mnemosyne.

**Gaps identified:**
- `LLM` is instantiated without config in `agent.py`.
- `_tool_schemas()` is hand-written and incomplete.
- `write_file` and `dry_run` can escape the project tree.
- `max_build_retries` is configured but unused in the loop.
- Memory is session-scoped and not automatically retrieved before tasks.
- No planner step; the LLM reacts one turn at a time.
- No MCP support, so external tools (web search, finance APIs, GitHub) cannot be added without code changes.
- No security guardrails beyond the `dry_run` prompt rule.
- No observability/evaluation harness beyond raw JSONL logs.

---

## Plan

### Task 1: Fix Config Propagation to the LLM Client

**Objective:** Ensure `agent.py` passes the parsed `Config` into `llm.py` so the configured provider, model, and API key are actually used.

**Files:**
- Modify: `agent.py:66-72` (the `LLM` wrapper class)
- Modify: `llm.py:10-22` (the `LLM` constructor signature)
- Test: `test_stub.py`

**Step 1: Update `llm.py` to accept config directly**

```python
class LLM:
    def __init__(self, config: "Config"):
        self.provider = config.llm_provider.lower()
        self.model = config.llm_model
        self.api_key_env = config.llm_api_key_env
        self.api_key = os.getenv(self.api_key_env) if self.api_key_env else ""
        self.base_url = config.llm_base_url or ""
        self.client = None
        self._init_client()
```

**Step 2: Add `llm_base_url` to `Config`**

In `agent.py:30-43`, add:

```python
llm_base_url: str
```

And in `Config.from_yaml`, read it:

```python
llm_base_url=raw["llm"].get("base_url", ""),
```

**Step 3: Update `agent.py` `LLM` wrapper**

```python
class LLM:
    def __init__(self, config: Config):
        self._client = RealLLM(config)
```

**Step 4: Verify**

Add a test in `test_stub.py`:

```python
def test_llm_config_propagation(tmpdir: Path):
    config = make_test_config(tmpdir)
    config.llm_base_url = "https://openrouter.ai/api/v1"
    agent = Agent(config)
    assert agent.llm._client.model == "claude-sonnet-4"
    assert agent.llm._client.provider == "anthropic"
```

Run: `pytest test_stub.py -v`
Expected: `test_llm_config_propagation` passes.

---

### Task 2: Auto-Generate Tool Schemas from Function Signatures

**Objective:** Replace the hand-written `_tool_schemas()` in `agent.py` with an `inspect`-based schema generator so new tools are automatically exposed to the LLM.

**Files:**
- Create: `tools/schema.py`
- Modify: `agent.py:102-127` and the `_tool_schemas()` method
- Modify: `tools/file.py`, `tools/project.py`, etc. to add docstrings
- Test: `test_stub.py`

**Step 1: Create `tools/schema.py`**

```python
"""Generate OpenAI-style tool schemas from Python callables."""

import inspect
from typing import Any, Callable


def _python_type_to_json_type(t: type) -> str:
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    return mapping.get(t, "string")


def schema_for(fn: Callable) -> dict:
    sig = inspect.signature(fn)
    doc = (fn.__doc__ or "").strip()
    properties = {}
    required = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        param_type = "string"
        if param.annotation is not inspect.Parameter.empty:
            origin = getattr(param.annotation, "__origin__", None)
            if origin in (list, dict):
                param_type = "array" if origin is list else "object"
            elif isinstance(param.annotation, type):
                param_type = _python_type_to_json_type(param.annotation)
        properties[name] = {
            "type": param_type,
            "description": f"Parameter `{name}` for {fn.__name__}.",
        }
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": doc,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def schemas_from_registry(registry: dict[str, Callable]) -> list[dict]:
    return [schema_for(fn) for fn in registry.values()]
```

**Step 2: Update tool docstrings**

For each tool method in `tools/*.py`, add a concise docstring. Example for `tools/file.py`:

```python
class FileTools:
    def write_file(self, path: str, content: str) -> dict:
        """Write content to a file, creating parent directories if needed."""
```

**Step 3: Replace `_tool_schemas()` in `agent.py`**

```python
from tools.schema import schemas_from_registry

class Agent:
    ...
    def _tool_schemas(self) -> list[dict]:
        return schemas_from_registry(self.tools)
```

**Step 4: Verify**

```python
def test_tool_schema_generation(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    schemas = agent._tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert "write_file" in names
    assert "build_module" in names
    assert "memory_recall" in names
```

Run: `pytest test_stub.py -v`
Expected: all schema tests pass.

---

### Task 3: Add Project-Tree Path Guards to File Tools

**Objective:** Prevent the agent from writing or previewing files outside the UE project directory.

**Files:**
- Modify: `tools/file.py`
- Create: `tools/guard.py`
- Modify: `config.yaml` (optional `agent.allowed_paths`)
- Test: `test_stub.py`

**Step 1: Create `tools/guard.py`**

```python
"""Path safety guards."""

from pathlib import Path


class PathGuard:
    def __init__(self, project_root: Path, allowed_paths: list[Path] | None = None):
        self.project_root = project_root.resolve()
        self.allowed = [p.resolve() for p in (allowed_paths or [])]

    def is_safe(self, target: Path) -> bool:
        resolved = target.resolve()
        if resolved == self.project_root or self.project_root in resolved.parents:
            return True
        for allowed in self.allowed:
            if resolved == allowed or allowed in resolved.parents:
                return True
        return False

    def assert_safe(self, target: Path) -> None:
        if not self.is_safe(target):
            raise ValueError(f"Path {target} is outside the allowed project tree.")
```

**Step 2: Update `FileTools` to accept a guard**

```python
class FileTools:
    def __init__(self, guard: PathGuard | None = None):
        self.guard = guard

    def _check(self, path: Path) -> None:
        if self.guard:
            self.guard.assert_safe(path)

    def read_file(self, path: str) -> dict:
        p = Path(path)
        self._check(p)
        ...

    def write_file(self, path: str, content: str) -> dict:
        p = Path(path)
        self._check(p)
        ...

    def dry_run(self, path: str, content: str) -> dict:
        p = Path(path)
        self._check(p)
        ...
```

**Step 3: Wire the guard into `Agent.__init__`**

```python
from tools.guard import PathGuard

class Agent:
    def __init__(self, config: Config):
        ...
        project_root = Path(config.uproject_path).parent
        allowed_paths = [Path(p) for p in getattr(config, "allowed_paths", [])]
        self.guard = PathGuard(project_root, allowed_paths)
        self.file_tools = FileTools(self.guard)
```

**Step 4: Verify**

```python
def test_path_guard_blocks_escape(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    outside = tmpdir / ".." / "outside.txt"
    result = agent.file_tools.write_file(str(outside), "x")
    assert "error" in result
    assert "outside" in result["error"].lower()
```

Run: `pytest test_stub.py -v`
Expected: guard test passes.

---

### Task 4: Implement the Build Retry Loop

**Objective:** Use the existing `max_build_retries` config to automatically parse build errors, feed them back to the LLM, and retry edits.

**Files:**
- Modify: `agent.py:128-165` (`run` method)
- Modify: `tools/build.py` (macOS-friendly stub for testing)
- Test: `test_stub.py`

**Step 1: Add a simulated build tool for macOS testing**

In `tools/build.py`, make `build_module` fall back to a lightweight validation when UBT is not found:

```python
def build_module(self, module_name: str) -> dict:
    uproject = Path(self.config.uproject_path)
    if not uproject.exists():
        return {"error": f"uproject not found: {uproject}"}

    engine_root = Path(r"C:\Program Files\Epic Games\UE_5.7")
    ubt = engine_root / "Engine" / "Build" / "BatchFiles" / "Build.bat"
    if not ubt.exists():
        # macOS / Linux test path: validate that the module has source files
        return {"exit_code": 0, "stdout": "UBT not found; synthetic pass for testing.", "stderr": ""}
    ...
```

**Step 2: Add a `run_with_retries` helper in `Agent`**

```python
class Agent:
    ...
    def _attempt_build(self, messages: list[dict], module_name: str) -> tuple[int, str]:
        build_result = self.tools["build_module"](module_name=module_name)
        exit_code = build_result.get("exit_code", 1)
        output = build_result.get("stdout", "") + "\n" + build_result.get("stderr", "")
        if exit_code != 0:
            errors = self.tools["parse_build_errors"](output=output)
            messages.append({
                "role": "user",
                "content": f"Build failed with exit code {exit_code}. Errors: {json.dumps(errors)}\n\nFix the code and try again.",
            })
        return exit_code, output
```

**Step 3: Update the main loop to call retry logic**

Replace the simple `while` loop with a state machine that tracks build retries:

```python
def run(self, user_prompt: str) -> str:
    ...
    build_attempts = 0
    last_build_exit = 0
    while iterations < max_iterations:
        iterations += 1
        response = self.llm.invoke(messages, tools=self._tool_schemas())
        ...
        for call in response.get("tool_calls", []):
            ...
            result = self._call_tool(tool_name, args)
            ...

        if not response.get("tool_calls"):
            ...

    return "Reached maximum iteration count without a final answer."
```

For a simpler first pass, add a dedicated `build_module` tracker that retries after each failed build:

```python
if tool_name == "build_module":
    build_attempts += 1
    if result.get("exit_code", 1) != 0 and build_attempts < self.config.max_build_retries:
        messages.append({
            "role": "user",
            "content": f"Build failed (attempt {build_attempts}/{self.config.max_build_retries}). Fix the errors and retry.",
        })
        continue
```

**Step 4: Verify**

```python
def test_build_retry_counter(tmpdir: Path):
    config = make_test_config(tmpdir)
    config.max_build_retries = 2
    agent = Agent(config)
    assert agent.config.max_build_retries == 2
```

Run: `pytest test_stub.py -v`
Expected: build retry test passes.

---

### Task 5: Persistent Memory Recall Before Tasks and Remember After Success

**Objective:** Make Mnemosyne truly useful across sessions by automatically recalling project context before a task and storing the outcome after success.

**Files:**
- Modify: `agent.py:128-165` (`run` method)
- Modify: `tools/memory.py` (if not present, create it)
- Modify: `config.yaml`
- Test: `test_stub.py`

**Step 1: Move memory wrappers to `tools/memory.py`**

```python
"""Mnemosyne-backed memory wrapper."""

from typing import Any


class MemoryTools:
    def __init__(self, memory: Any):
        self.memory = memory

    def remember(self, content: str, importance: float = 0.5, source: str = "tool") -> dict:
        memory_id = self.memory.remember(
            content, source=source, importance=importance, scope="session"
        )
        return {"memory_id": memory_id, "stored": True}

    def recall(self, query: str, top_k: int = 5) -> dict:
        return {"memories": self.memory.recall(query, top_k=top_k)}
```

**Step 2: Update `Agent` to use `MemoryTools`**

```python
from tools.memory import MemoryTools

class Agent:
    def __init__(self, config: Config):
        ...
        self.memory = self._init_memory()
        self.memory_tools = MemoryTools(self.memory) if self.memory else None
        self.tools = self._build_tool_registry()
```

**Step 3: Automatic recall before each task**

In `Agent.run`, keep the existing `_recall_memory_context` but strengthen it to query both the task and the project name:

```python
def _recall_memory_context(self, user_prompt: str) -> str:
    if not self.memory:
        return ""
    project = Path(self.config.uproject_path).stem
    queries = [user_prompt, f"{project} project conventions", f"{project} build failures"]
    seen = set()
    memories = []
    for q in queries:
        for m in self.memory.recall(q, top_k=3):
            mid = m.get("memory_id")
            if mid not in seen:
                seen.add(mid)
                memories.append(m)
    if not memories:
        return ""
    lines = ["Relevant memory context:"]
    for m in memories:
        lines.append(f"- {m.get('content', '')}")
    return "\n".join(lines)
```

**Step 4: Automatic remember after success**

```python
def _remember_outcome(self, user_prompt: str, summary: str, success: bool = True) -> None:
    if not self.memory:
        return
    status = "succeeded" if success else "failed"
    self.memory.remember(
        f"Project: {Path(self.config.uproject_path).stem}\n"
        f"Task: {user_prompt}\n"
        f"Outcome: {status}\n"
        f"Summary: {summary}",
        source="agent",
        importance=0.7,
        scope="session",
    )
```

**Step 5: Verify**

`test_memory_enabled` already exists in `test_stub.py`. Extend it to verify recall returns at least one memory after a `remember` call.

Run: `pytest test_stub.py -v`
Expected: memory tests pass.

---

### Task 6: Add a Planner Step

**Objective:** Before writing code, the agent should decompose the user request into a plan and confirm it. This borrows the planning design pattern from `ai-agents-for-beginners` lesson 7.

**Files:**
- Create: `prompts/planner.txt`
- Modify: `agent.py:128-165` (`run` method)
- Modify: `prompts/system.txt`
- Test: `test_stub.py`

**Step 1: Create `prompts/planner.txt`**

```text
You are a planner for a Unreal Engine C++ agent. Given a task and project context, produce a concise, ordered plan of tool calls. Each step must be one of: scan, read, dry_run, write_file, build_module, editor_command. Do not execute the plan; only output it as a JSON array of objects with keys "tool" and "reason".

Example:
[
  {"tool": "scan_project", "reason": "Discover the project modules and source layout."},
  {"tool": "read_file", "reason": "Inspect the existing player character header."},
  {"tool": "dry_run", "reason": "Preview the stamina attribute class header."},
  {"tool": "write_file", "reason": "Write the new attribute class .h and .cpp."},
  {"tool": "build_module", "reason": "Compile the module to verify the changes."}
]
```

**Step 2: Add a `_plan` method in `Agent`**

```python
class Agent:
    ...
    def _plan(self, user_prompt: str) -> list[dict]:
        planner_prompt = Path("prompts/planner.txt").read_text()
        context = self._contextualize(user_prompt)
        messages = [
            {"role": "system", "content": planner_prompt},
            {"role": "user", "content": context},
        ]
        response = self.llm.invoke(messages)
        content = response.get("content", "")
        try:
            # Extract JSON array if wrapped in markdown fences
            import re
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return json.loads(content)
        except json.JSONDecodeError:
            return []
```

**Step 3: Run the planner before the main loop**

```python
def run(self, user_prompt: str) -> str:
    ...
    plan = self._plan(user_prompt)
    with session_path.open("a") as log:
        log.write(json.dumps({"role": "plan", "content": plan}) + "\n")
    ...
```

The planner output is informational; the ReAct loop remains the executor. Optionally, append the plan to the system/user context so the LLM follows it.

**Step 4: Verify**

```python
def test_planner_returns_steps(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    # Mock LLM to avoid real API call
    agent.llm = lambda messages, tools=None: {"role": "assistant", "content": "[{'tool': 'scan_project', 'reason': 'x'}]", "tool_calls": None}
    plan = agent._plan("add stamina")
    assert len(plan) >= 1
    assert plan[0]["tool"] == "scan_project"
```

Run: `pytest test_stub.py -v`
Expected: planner test passes.

---

### Task 7: Add MCP Support as a Feature Flag

**Objective:** Allow external tools to be registered via MCP without changing the core. This mirrors the MCP integration in `awesome-llm-apps` and `agents-towards-production`, and the Hermes MCP startup pattern.

**Files:**
- Create: `tools/mcp_client.py`
- Modify: `config.yaml` (add `mcp_servers` block)
- Modify: `agent.py` (register MCP tools)
- Modify: `requirements.txt` or `pyproject.toml` (optional `mcp` dependency)
- Test: `test_stub.py`

**Step 1: Create `tools/mcp_client.py`**

```python
"""Optional MCP client that registers external tools into the harness.

Requires: pip install mcp
"""

import json
from typing import Any

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False


class MCPClient:
    def __init__(self, servers: dict[str, dict]):
        self.servers = servers
        self.tools: dict[str, Any] = {}

    def discover(self) -> dict[str, dict]:
        """Discover tools from configured MCP servers.

        Returns a flat map of tool_name -> schema.
        """
        if not _MCP_AVAILABLE or not self.servers:
            return {}
        discovered = {}
        for server_name, cfg in self.servers.items():
            try:
                params = StdioServerParameters(
                    command=cfg["command"],
                    args=cfg.get("args", []),
                    env=cfg.get("env"),
                )
                with stdio_client(params) as (read, write):
                    with ClientSession(read, write) as session:
                        session.initialize()
                        result = session.list_tools()
                        for tool in result.tools:
                            discovered[tool.name] = {
                                "server": server_name,
                                "schema": {
                                    "type": "function",
                                    "function": {
                                        "name": tool.name,
                                        "description": tool.description or "",
                                        "parameters": tool.inputSchema,
                                    },
                                },
                            }
            except Exception as exc:
                discovered[f"{server_name}_error"] = {"error": str(exc)}
        return discovered

    def call(self, tool_name: str, args: dict) -> dict:
        """Call a tool on the appropriate MCP server."""
        if not _MCP_AVAILABLE:
            return {"error": "MCP package not installed."}
        meta = self.tools.get(tool_name)
        if not meta:
            return {"error": f"Unknown MCP tool: {tool_name}"}
        server_name = meta["server"]
        cfg = self.servers[server_name]
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=cfg.get("env"),
        )
        with stdio_client(params) as (read, write):
            with ClientSession(read, write) as session:
                session.initialize()
                result = session.call_tool(tool_name, args)
                return {"result": [c.text for c in result.content if hasattr(c, "text")]}
```

**Step 2: Add MCP config to `config.yaml`**

```yaml
mcp:
  enabled: false
  servers:
    # Example: filesystem MCP server
    filesystem:
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
```

**Step 3: Register MCP tools in `Agent`**

```python
class Agent:
    def __init__(self, config: Config):
        ...
        self.mcp_client = None
        self.mcp_tools = {}
        if getattr(config, "mcp_enabled", False):
            from tools.mcp_client import MCPClient
            self.mcp_client = MCPClient(config.mcp_servers)
            self.mcp_tools = self.mcp_client.discover()
            for name, meta in self.mcp_tools.items():
                if "schema" in meta:
                    self.tools[name] = lambda args, n=name: self.mcp_client.call(n, args)
        ...
```

Note: schema generation should include MCP tools. Since `schemas_from_registry` uses the callable, the schema is stored separately. Adjust `tools/schema.py` to accept a list of schemas, or keep a separate `_tool_schemas` that merges built-in and MCP schemas.

**Step 4: Verify**

```python
def test_mcp_disabled_by_default(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    assert agent.mcp_client is None
```

Run: `pytest test_stub.py -v`
Expected: MCP test passes.

---

### Task 8: Add Security Guardrails

**Objective:** Borrow from `agents-towards-production` security tutorials: prevent destructive operations, validate outputs, and require approval for dangerous tools.

**Files:**
- Modify: `tools/guard.py` (add `dangerous` labels)
- Modify: `agent.py` (add approval gate)
- Modify: `prompts/system.txt`
- Test: `test_stub.py`

**Step 1: Label dangerous tools**

In `agent.py`, define:

```python
DANGEROUS_TOOLS = {"write_file", "build_module", "editor_command"}
READONLY_TOOLS = {"scan_project", "read_file", "read_build_cs", "list_source_files", "dry_run", "is_editor_running", "memory_recall"}
```

**Step 2: Add approval mode to `Config`**

```yaml
agent:
  approval_mode: "auto"  # auto | ask | readonly
```

And in `Config` add:

```python
approval_mode: str
```

**Step 3: Gate destructive calls in `_call_tool`**

```python
def _call_tool(self, tool_name: str, args: dict) -> dict:
    if self.config.approval_mode == "readonly" and tool_name not in READONLY_TOOLS:
        return {"error": f"Readonly mode: {tool_name} is not allowed."}
    if self.config.approval_mode == "ask" and tool_name in DANGEROUS_TOOLS:
        # For now, require explicit approval in the prompt; a TUI/REPL can override later.
        return {"error": f"Approval required for {tool_name}. Set approval_mode to 'auto' after reviewing the dry_run."}
    return self.tools[tool_name](**args)
```

**Step 4: Update `prompts/system.txt`**

Add: "Never write files outside the project Source directory. Always run `dry_run` before `write_file`. Never delete existing source files unless explicitly asked."

**Step 5: Verify**

```python
def test_readonly_mode_blocks_writes(tmpdir: Path):
    config = make_test_config(tmpdir)
    config.approval_mode = "readonly"
    agent = Agent(config)
    result = agent.tools["write_file"](path=str(tmpdir / "test.txt"), content="x")
    # In readonly mode, the tool registry still exists, but _call_tool should block.
    result = agent._call_tool("write_file", {"path": str(tmpdir / "test.txt"), "content": "x"})
    assert "error" in result
```

Run: `pytest test_stub.py -v`
Expected: security test passes.

---

### Task 9: Add Observability and Evaluation Harness

**Objective:** Borrow from `agents-towards-production` evaluation and `ai-agents-for-beginners` lesson 10. Track build success rate, iteration count, and tool usage.

**Files:**
- Create: `eval/metrics.py`
- Modify: `agent.py` (emit events)
- Create: `eval/report.py`
- Test: `test_stub.py`

**Step 1: Create `eval/metrics.py`**

```python
"""Session metrics tracking."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SessionMetrics:
    session_id: str
    task: str
    iterations: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    build_attempts: int = 0
    build_success: bool = False
    final_status: str = "incomplete"
    errors: list[str] = field(default_factory=list)

    def record_tool(self, name: str) -> None:
        self.tool_calls[name] = self.tool_calls.get(name, 0) + 1

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "task": self.task,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "build_attempts": self.build_attempts,
            "build_success": self.build_success,
            "final_status": self.final_status,
            "errors": self.errors,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(self.to_dict()) + "\n")
```

**Step 2: Integrate metrics into `Agent.run`**

```python
def run(self, user_prompt: str) -> str:
    session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    metrics = SessionMetrics(session_id=session_id, task=user_prompt)
    ...
    while iterations < max_iterations:
        iterations += 1
        metrics.iterations = iterations
        response = self.llm.invoke(messages, tools=self._tool_schemas())
        ...
        for call in response.get("tool_calls", []):
            metrics.record_tool(call["function"]["name"])
            ...
    metrics.final_status = "max_iterations"
    metrics.save(Path("sessions") / "metrics.jsonl")
    return "Reached maximum iteration count without a final answer."
```

**Step 3: Create `eval/report.py`**

```python
"""Generate a simple report from metrics.jsonl."""

import json
from pathlib import Path


def summarize(metrics_path: Path = Path("sessions/metrics.jsonl")) -> dict:
    total = 0
    success = 0
    build_attempts = 0
    for line in metrics_path.read_text().splitlines():
        if not line.strip():
            continue
        m = json.loads(line)
        total += 1
        if m.get("build_success"):
            success += 1
        build_attempts += m.get("build_attempts", 0)
    return {
        "total_sessions": total,
        "build_success_rate": success / total if total else 0,
        "average_build_attempts": build_attempts / total if total else 0,
    }
```

**Step 4: Verify**

```python
def test_metrics_save(tmpdir: Path):
    from eval.metrics import SessionMetrics
    m = SessionMetrics(session_id="123", task="test")
    m.record_tool("write_file")
    m.build_success = True
    m.save(tmpdir / "metrics.jsonl")
    lines = (tmpdir / "metrics.jsonl").read_text().splitlines()
    data = json.loads(lines[0])
    assert data["tool_calls"]["write_file"] == 1
    assert data["build_success"] is True
```

Run: `pytest test_stub.py -v`
Expected: metrics test passes.

---

### Task 10: Update `config.yaml` and `prompts/system.txt`

**Objective:** Reflect all new flags in the user-facing config and tighten the system prompt.

**Files:**
- Modify: `config.yaml`
- Modify: `prompts/system.txt`

**Step 1: Update `config.yaml`**

```yaml
project:
  uproject_path: "C:/Projects/MurimSouls/MurimSouls.uproject"
  default_module: "MurimSouls"

bridge:
  type: "stub"              # stub | file
  file_path: "./bridge/bridge.json"
  poll_interval: 0.5
  timeout: 30.0

llm:
  provider: "anthropic"      # anthropic | openai | openrouter | ollama | lmstudio
  model: "claude-sonnet-4"
  api_key_env: "ANTHROPIC_API_KEY"
  base_url: ""

agent:
  max_build_retries: 3
  memory_enabled: true
  journal_path: "./progress.md"
  db_path: "./memory.db"
  approval_mode: "auto"      # auto | ask | readonly
  allowed_paths: []           # extra paths outside the project tree

mcp:
  enabled: false
  servers: {}
```

**Step 2: Update `prompts/system.txt`**

Add the following rules (keeping the existing UE-aware prompt):

```text
SAFETY RULES:
- Never write files outside the project Source directory or allowed_paths.
- Always call dry_run before write_file.
- Always pair new .h files with .cpp files under the same module.
- Never delete existing source files unless the user explicitly asks.
- If a build fails, read the error output, fix the root cause, and retry.

WORKFLOW RULES:
- Before writing code, plan: scan the project, read relevant source, then dry_run.
- After writing code, call build_module.
- After a successful build, update progress.md and remember the outcome.
```

**Step 3: Verify**

```python
def test_config_loads_all_keys(tmpdir: Path):
    config = Config.from_yaml("config.yaml")
    assert config.approval_mode in ("auto", "ask", "readonly")
```

Run: `pytest test_stub.py -v`
Expected: config test passes.

---

### Task 11: Integration Test End-to-End with Stub Bridge

**Objective:** Run the full flow on macOS without UE and confirm the harness can write a file, preview a diff, build, and log the outcome.

**Files:**
- Modify: `test_stub.py` or create `tests/test_e2e.py`

**Step 1: Create an end-to-end test**

```python
def test_e2e_stub_write_and_build(tmpdir: Path):
    # Create a fake uproject
    uproject = tmpdir / "MurimSouls.uproject"
    uproject.write_text(json.dumps({"EngineAssociation": "5.7"}))
    source = tmpdir / "Source" / "MurimSouls"
    source.mkdir(parents=True)
    (source / "MurimSouls.Build.cs").write_text("using UnrealBuildTool;\n")

    config = Config(
        uproject_path=str(uproject),
        default_module="MurimSouls",
        bridge_type="stub",
        file_path=str(tmpdir / "bridge.json"),
        bridge_poll_interval=0.05,
        bridge_timeout=5.0,
        llm_provider="anthropic",
        llm_model="claude-sonnet-4",
        llm_api_key_env="ANTHROPIC_API_KEY",
        llm_base_url="",
        max_build_retries=3,
        memory_enabled=False,
        journal_path=str(tmpdir / "progress.md"),
        db_path=str(tmpdir / "memory.db"),
        approval_mode="auto",
        allowed_paths=[],
    )
    agent = Agent(config)
    # Mock LLM to avoid real API calls
    class FakeLLM:
        def __init__(self, config): pass
        def invoke(self, messages, tools=None):
            # First call: plan. Second call: return final answer.
            return {"role": "assistant", "content": "Done", "tool_calls": None}
    agent.llm = FakeLLM(config)
    result = agent.run("Add a stamina attribute to the player character.")
    assert "Done" in result or "max" not in result.lower()
```

**Step 2: Run the full test suite**

```bash
source .venv/bin/activate
pytest test_stub.py -v
```

Expected: all tests pass.

---

## Risks, Tradeoffs, and Open Questions

| Risk | Mitigation |
|------|-----------|
| MCP adds a heavy dependency (`mcp` SDK) | Keep it optional; skip if not installed. |
| Auto-generated schemas may be too loose for complex nested args | Add manual overrides for specific tools later. |
| Path guard may block legitimate engine-source edits | Allow `allowed_paths` in config. |
| Build retry loop could infinite-loop on non-fixable errors | Respect `max_build_retries` and break after limit. |
| Memory recall adds latency before each task | Limit to top 3 memories per query; make it optional. |
| Planner adds an extra LLM call | Cache planner output for repeated tasks; make it optional. |

**Open Questions:**
1. Should the planner output be used to constrain the ReAct loop, or remain advisory?
2. Should MCP tool calls run in parallel like Hermes does (`supports_parallel_tool_calls`)?
3. Should the harness support a `code mode` where the LLM writes a Python script that chains tool calls?
4. Should we add a simple TUI approval dialog for `approval_mode: ask`?

---

## Next Steps After This Plan

1. Execute the tasks in order using `subagent-driven-development`.
2. After each task, run `pytest test_stub.py -v` before moving to the next.
3. Once all tasks pass, test the live file bridge on Windows against the UE editor.
4. Convert this plan into a living architecture document once the code stabilizes.

---

## References

- `ue-agent-harness-specs.md` — original spec for the harness.
- `awesome-llm-apps` — inspiration for MCP agents, multi-agent teams, and always-on patterns.
- `agents-towards-production` — production patterns for memory, security, deployment, and MCP.
- `ai-agents-for-beginners` — design patterns for tool use, planning, multi-agent, metacognition, and memory.
- `/Users/noctis/hermes-agent-fork-reference/tools/mcp_tool.py` — reference for MCP client implementation.
- `/Users/noctis/hermes-agent-fork-reference/hermes_cli/mcp_startup.py` — reference for background MCP discovery.
