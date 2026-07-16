"""UE Agent Harness — main loop."""

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from tools.bridge import Bridge
from tools.build import BuildTools
from tools.editor import EditorTools
from tools.file import FileTools
from tools.guard import PathGuard
from tools.project import ProjectTools
from tools.schema import schemas_from_registry
from llm import LLM as RealLLM
from eval.metrics import SessionMetrics


try:
    from mnemosyne.core.memory import Mnemosyne
    HAS_MNEMOSYNE = True
except Exception:  # pragma: no cover
    HAS_MNEMOSYNE = False


DANGEROUS_TOOLS = {"write_file", "build_module", "editor_command"}
READONLY_TOOLS = {"scan_project", "read_file", "read_build_cs", "list_source_files", "dry_run", "is_editor_running", "memory_recall"}


@dataclass
class Config:
    uproject_path: str
    default_module: str
    bridge_type: str
    bridge_file_path: str
    bridge_poll_interval: float
    bridge_timeout: float
    llm_provider: str
    llm_model: str
    llm_api_key_env: str
    llm_base_url: str
    max_build_retries: int
    memory_enabled: bool
    journal_path: str
    db_path: str
    approval_mode: str = "auto"
    allowed_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str = "config.yaml") -> "Config":
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
        return cls(
            uproject_path=raw["project"]["uproject_path"],
            default_module=raw["project"]["default_module"],
            bridge_type=raw["bridge"]["type"],
            bridge_file_path=raw["bridge"]["file_path"],
            bridge_poll_interval=raw["bridge"]["poll_interval"],
            bridge_timeout=raw["bridge"]["timeout"],
            llm_provider=raw["llm"]["provider"],
            llm_model=raw["llm"]["model"],
            llm_api_key_env=raw["llm"]["api_key_env"],
            llm_base_url=raw["llm"].get("base_url", ""),
            max_build_retries=raw["agent"]["max_build_retries"],
            memory_enabled=raw["agent"]["memory_enabled"],
            journal_path=raw["agent"]["journal_path"],
            db_path=raw["agent"].get("db_path", "./memory.db"),
            approval_mode=raw["agent"].get("approval_mode", "auto"),
            allowed_paths=raw["agent"].get("allowed_paths", []),
        )


class LLM:
    """Pluggable LLM caller."""
    def __init__(self, config: Config):
        self._client = RealLLM(config)

    def invoke(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        return self._client.invoke(messages, tools)


class Agent:
    def __init__(self, config: Config):
        self.config = config
        self.llm = LLM(config)
        self.bridge = Bridge(
            bridge_type=config.bridge_type,
            file_path=config.bridge_file_path,
            poll_interval=config.bridge_poll_interval,
            timeout=config.bridge_timeout,
        )
        self.project_tools = ProjectTools(config)
        project_root = Path(config.uproject_path).parent
        allowed_paths = [Path(p) for p in getattr(config, "allowed_paths", [])]
        self.guard = PathGuard(project_root, allowed_paths)
        self.file_tools = FileTools(self.guard)
        self.build_tools = BuildTools(config)
        self.editor_tools = EditorTools(self.bridge)
        self.memory = self._init_memory()
        self.tools = self._build_tool_registry()
        self.mcp_client = None
        self.mcp_tool_schemas: list[dict] = []
        if getattr(config, "mcp_enabled", False):
            from tools.mcp_client import MCPClient
            self.mcp_client = MCPClient(getattr(config, "mcp_servers", {}))
            for name, meta in self.mcp_client.discover().items():
                if "schema" in meta:
                    self.tools[name] = lambda args, n=name: self.mcp_client.call(n, args)
                    self.mcp_tool_schemas.append(meta["schema"])

    def _init_memory(self) -> Any:
        if not self.config.memory_enabled or not HAS_MNEMOSYNE:
            return None
        return Mnemosyne(
            session_id="agentunreal",
            db_path=Path(self.config.db_path),
            author_id="agentunreal",
            author_type="harness",
        )

    def _build_tool_registry(self) -> dict:
        registry = {
            "scan_project": self.project_tools.scan_project,
            "read_build_cs": self.project_tools.read_build_cs,
            "read_file": self.file_tools.read_file,
            "write_file": self.file_tools.write_file,
            "list_source_files": self.file_tools.list_source_files,
            "dry_run": self.file_tools.dry_run,
            "build_module": self.build_tools.build_module,
            "parse_build_errors": self.build_tools.parse_build_errors,
            "editor_command": self.editor_tools.editor_command,
            "compile_blueprints": self.editor_tools.compile_blueprints,
            "is_editor_running": self.editor_tools.is_editor_running,
        }
        if self.memory:
            registry["memory_remember"] = self._memory_remember
            registry["memory_recall"] = self._memory_recall
        return registry

    def _memory_remember(self, content: str, importance: float = 0.5, source: str = "tool") -> dict:
        memory_id = self.memory.remember(content, source=source, importance=importance, scope="session")
        return {"memory_id": memory_id, "stored": True}

    def _memory_recall(self, query: str, top_k: int = 5) -> dict:
        return {"memories": self.memory.recall(query, top_k=top_k)}

    def _tool_schemas(self) -> list[dict]:
        return schemas_from_registry(self.tools) + self.mcp_tool_schemas

    def _call_tool(self, name: str, args: dict) -> Any:
        if self.config.approval_mode == "readonly" and name not in READONLY_TOOLS:
            return {"error": f"Readonly mode: {name} is not allowed."}
        if self.config.approval_mode == "ask" and name in DANGEROUS_TOOLS:
            return {"error": f"Approval required for {name}. Set approval_mode to 'auto' after reviewing the dry_run."}
        tool = self.tools.get(name)
        if not tool:
            return {"error": f"Unknown tool: {name}"}
        try:
            return {"result": tool(**args)}
        except Exception as e:
            return {"error": str(e)}

    def run(self, user_prompt: str) -> str:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        session_path = Path("sessions") / f"{session_id}.jsonl"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        metrics = SessionMetrics(session_id=session_id, task=user_prompt)

        context = self._contextualize(user_prompt)
        memory_context = self._recall_memory_context(user_prompt)
        if memory_context:
            context = f"{memory_context}\n\n{context}"

        plan = self._plan(user_prompt)
        if plan:
            plan_summary = "Plan:\n" + "\n".join(
                f"{i+1}. {step.get('tool', '?')} — {step.get('reason', '')}"
                for i, step in enumerate(plan)
            )
            context = f"{context}\n\n{plan_summary}"

        messages = [
            {"role": "system", "content": self._load_system_prompt()},
            {"role": "user", "content": context},
        ]

        with session_path.open("a") as log:
            log.write(json.dumps({"role": "user", "content": user_prompt}) + "\n")
            log.write(json.dumps({"role": "plan", "content": plan}) + "\n")

            iterations = 0
            max_iterations = 10
            build_attempts = 0
            while iterations < max_iterations:
                iterations += 1
                metrics.iterations = iterations
                response = self.llm.invoke(messages, tools=self._tool_schemas())
                log.write(json.dumps({"role": "assistant", "content": response.get("content"), "tool_calls": response.get("tool_calls")}) + "\n")

                if not response.get("tool_calls"):
                    metrics.final_status = "success"
                    metrics.save(Path("sessions") / "metrics.jsonl")
                    self._append_journal(user_prompt, response["content"])
                    self._remember_outcome(user_prompt, response["content"])
                    return response["content"]

                for call in response.get("tool_calls", []):
                    tool_name = call["function"]["name"]
                    metrics.record_tool(tool_name)
                    args = json.loads(call["function"]["arguments"])
                    result = self._call_tool(tool_name, args)
                    if isinstance(result, dict) and "error" in result:
                        metrics.errors.append(f"{tool_name}: {result['error']}")
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
                    log.write(json.dumps({"role": "tool", "tool_call_id": call["id"], "content": result}) + "\n")

                    if tool_name == "build_module":
                        build_attempts += 1
                        metrics.build_attempts = build_attempts
                        if result.get("result", {}).get("exit_code", 1) == 0:
                            metrics.build_success = True
                        if result.get("result", {}).get("exit_code", 1) != 0 and build_attempts < self.config.max_build_retries:
                            errors = self.tools["parse_build_errors"](output=result.get("result", {}).get("stdout", "") + "\n" + result.get("result", {}).get("stderr", ""))
                            messages.append({
                                "role": "user",
                                "content": f"Build failed (attempt {build_attempts}/{self.config.max_build_retries}). Errors: {json.dumps(errors)}\n\nFix the code and retry.",
                            })
                            break

        metrics.final_status = "max_iterations"
        metrics.save(Path("sessions") / "metrics.jsonl")
        return "Reached maximum iteration count without a final answer."

    def _load_system_prompt(self) -> str:
        path = Path("prompts/system.txt")
        if path.exists():
            return path.read_text()
        return "You are a helpful coding assistant."

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
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return json.loads(content)
        except json.JSONDecodeError:
            return []

    def _contextualize(self, user_prompt: str) -> str:
        project = self.project_tools.scan_project(self.config.uproject_path)
        return (
            f"Project: {json.dumps(project)}\n\n"
            f"Task: {user_prompt}\n\n"
            "When proposing a new file or edit, always call `dry_run` first. "
            "After the dry_run result is returned, then call `write_file`."
        )

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

    def _remember_outcome(self, user_prompt: str, summary: str, success: bool = True) -> None:
        if not self.memory:
            return
        try:
            status = "succeeded" if success else "failed"
            project = Path(self.config.uproject_path).stem
            self.memory.remember(
                f"Project: {project}\n"
                f"Task: {user_prompt}\n"
                f"Outcome: {status}\n"
                f"Summary: {summary}",
                source="agent",
                importance=0.7,
                scope="session",
            )
        except Exception:
            pass

    def _append_journal(self, prompt: str, summary: str) -> None:
        path = Path(self.config.journal_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(f"\n## {datetime.now(timezone.utc).isoformat()}\n\n")
            f.write(f"**Task:** {prompt}\n\n")
            f.write(f"**Summary:** {summary}\n")


if __name__ == "__main__":
    config = Config.from_yaml()
    agent = Agent(config)
    result = agent.run("Add a stamina attribute to the player character.")
    print(result)
