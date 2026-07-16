"""Self-test for the stub-bridge mode of the UE agent harness."""

import json
from pathlib import Path

from agent import Agent, Config


def make_test_config(tmpdir: Path) -> Config:
    return Config(
        uproject_path=str(tmpdir / "MurimSouls.uproject"),
        default_module="MurimSouls",
        bridge_type="stub",
        bridge_file_path=str(tmpdir / "bridge.json"),
        bridge_poll_interval=0.05,
        bridge_timeout=5.0,
        llm_provider="anthropic",
        llm_model="claude-sonnet-4",
        llm_api_key_env="ANTHROPIC_API_KEY",
        llm_base_url="https://openrouter.ai/api/v1",
        max_build_retries=3,
        memory_enabled=False,
        journal_path=str(tmpdir / "progress.md"),
        db_path=str(tmpdir / "memory.db"),
        approval_mode="auto",
        allowed_paths=[],
    )


def test_stub_bridge_roundtrip(tmpdir: Path):
    from tools.bridge import Bridge
    bridge = Bridge(
        bridge_type="stub",
        file_path=str(tmpdir / "bridge.json"),
        poll_interval=0.05,
        timeout=5.0,
    )
    resp = bridge.request("editor_command", {"command": "foo"})
    assert resp.get("executed") == "foo"

    resp = bridge.request("compile_blueprints")
    assert resp.get("compiled") is True

    resp = bridge.request("is_editor_running")
    assert resp.get("running") is True


def test_scan_missing_project(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    result = agent.project_tools.scan_project(config.uproject_path)
    assert result.get("error") is not None


def test_dry_run_preview(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    target = tmpdir / "NewFile.txt"
    result = agent.file_tools.dry_run(str(target), "hello world\n")
    assert result["would_create"] is True
    assert "hello world" in result["diff"]


def test_memory_enabled(tmpdir: Path):
    config = make_test_config(tmpdir)
    config.memory_enabled = True
    agent = Agent(config)
    assert agent.memory is not None
    assert "memory_remember" in agent.tools
    assert "memory_recall" in agent.tools

    result = agent.tools["memory_remember"](content="Player stamina attribute added", importance=0.7)
    assert result["stored"] is True

    result = agent.tools["memory_recall"]("stamina player")
    assert len(result["memories"]) >= 1


def test_llm_config_propagation(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    assert agent.llm._client.provider == "anthropic"
    assert agent.llm._client.model == "claude-sonnet-4"
    assert agent.llm._client.base_url == "https://openrouter.ai/api/v1"


def test_tool_schema_generation(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    schemas = agent._tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert "write_file" in names
    assert "build_module" in names


def test_path_guard_blocks_escape(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    outside = Path(str(tmpdir)) / ".." / "outside.txt"
    outside = outside.resolve()
    result = agent.file_tools.write_file(str(outside), "x")
    assert "error" in result


def test_readonly_mode_blocks_writes(tmpdir: Path):
    config = make_test_config(tmpdir)
    config.approval_mode = "readonly"
    agent = Agent(config)
    inside = Path(str(tmpdir)) / "test.txt"
    result = agent._call_tool("write_file", {"path": str(inside), "content": "x"})
    assert "error" in result


def test_build_retry_counter(tmpdir: Path):
    config = make_test_config(tmpdir)
    config.max_build_retries = 2
    agent = Agent(config)
    assert agent.config.max_build_retries == 2


def test_planner_returns_steps(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)

    class FakeLLM:
        def invoke(self, messages, tools=None):
            return {"role": "assistant", "content": '```json\n[{"tool": "scan_project", "reason": "Discover the project layout."}]\n```', "tool_calls": None}

    agent.llm = FakeLLM()
    plan = agent._plan("add stamina")
    assert len(plan) >= 1
    assert plan[0]["tool"] == "scan_project"


def test_config_yaml_loads():
    config = Config.from_yaml("config.yaml")
    assert config.approval_mode in ("auto", "ask", "readonly")
    assert config.uproject_path
    assert config.default_module


def test_e2e_stub_write_and_build(tmpdir: Path):
    tmpdir = Path(str(tmpdir))
    uproject = tmpdir / "MurimSouls.uproject"
    uproject.write_text(json.dumps({"EngineAssociation": "5.7"}), encoding="utf-8")
    source = tmpdir / "Source" / "MurimSouls"
    source.mkdir(parents=True)
    (source / "MurimSouls.Build.cs").write_text("using UnrealBuildTool;\n", encoding="utf-8")

    config = make_test_config(tmpdir)
    agent = Agent(config)

    class FakeLLM:
        def __init__(self, config): pass
        def invoke(self, messages, tools=None):
            return {"role": "assistant", "content": "Done", "tool_calls": None}

    agent.llm = FakeLLM(config)
    result = agent.run("Add a stamina attribute to the player character.")
    assert "Done" in result
    # Verify progress.md was written by agent
    progress = tmpdir / "progress.md"
    assert progress.exists(), "progress.md should have been written by agent"
    content = progress.read_text()
    assert "Add a stamina attribute" in content


def test_mcp_disabled_by_default(tmpdir: Path):
    config = make_test_config(tmpdir)
    agent = Agent(config)
    assert agent.mcp_client is None


def test_metrics_save(tmpdir: Path):
    from eval.metrics import SessionMetrics
    tmpdir = Path(str(tmpdir))
    m = SessionMetrics(session_id="123", task="test")
    m.record_tool("write_file")
    m.build_success = True
    m.save(tmpdir / "metrics.jsonl")
    lines = (tmpdir / "metrics.jsonl").read_text().splitlines()
    data = json.loads(lines[0])
    assert data["tool_calls"]["write_file"] == 1
    assert data["build_success"] is True


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        test_stub_bridge_roundtrip(p)
        test_scan_missing_project(p)
        test_dry_run_preview(p)
        test_memory_enabled(p)
        test_llm_config_propagation(p)
        test_tool_schema_generation(p)
        test_path_guard_blocks_escape(p)
        test_readonly_mode_blocks_writes(p)
        test_build_retry_counter(p)
        test_e2e_stub_write_and_build(p)
        print("All tests passed.")
