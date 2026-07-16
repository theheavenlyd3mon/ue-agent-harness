"""End-to-end integration test for the UE Agent Harness (stub-bridge mode)."""

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


def test_e2e_stub_write_and_build(tmpdir: Path):
    """Fake UE project -> run agent -> verify success + progress.md logging. Stub bridge, no real editor."""
    tmpdir = Path(str(tmpdir))
    (tmpdir / "MurimSouls.uproject").write_text(
        json.dumps({"EngineAssociation": "5.7"}), encoding="utf-8"
    )
    source = tmpdir / "Source" / "MurimSouls"
    source.mkdir(parents=True)
    (source / "MurimSouls.Build.cs").write_text("using UnrealBuildTool;\n", encoding="utf-8")

    agent = Agent(make_test_config(tmpdir))

    class FakeLLM:  # ponytail: minimal stub, no plan-return needed; planner parse-fail -> [] is fine
        def __init__(self, config):
            pass

        def invoke(self, messages, tools=None):
            return {"role": "assistant", "content": "Done", "tool_calls": None}

    agent.llm = FakeLLM(None)
    result = agent.run("Add a stamina attribute to the player character.")
    assert "Done" in result

    progress = tmpdir / "progress.md"
    assert progress.exists(), "progress.md should have been written by agent"
    assert "Add a stamina attribute" in progress.read_text()
