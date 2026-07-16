# AgentUnreal harness vs. `agentu` — comparison report

## What `agentu` is

`agentu` (Hemanth HM, https://github.com/hemanth/agentu, PyPI: `agentu`, v2.2.0, MIT) is a general-purpose Python agent runtime. It is not tied to any engine; it provides a batteries-included framework for building, running, and observing autonomous LLM agents.

## Core features of `agentu`

- **ReAct loop:** `await agent.infer(...)` drives multi-turn tool-use / self-correction.
- **Tool isolation:** subprocess sandboxing with `read_tools`/`write_tools`, timeout, memory limit, network blocking.
- **Permission scoping:** `ToolPermission.READONLY/WRITE/DANGEROUS`, `allow_dangerous`, `mode="ask-writes"`.
- **Code mode:** LLM writes Python code that chains tool calls, inspired by Cloudflare’s Code Mode.
- **Guardrails:** output guardrails with auto-retry (e.g. PII, content filter) up to `max_corrections`.
- **Hooks:** pre/post-tool and `on_stop` callbacks with `ALLOW/DENY/MODIFY` actions.
- **Structured outputs:** Pydantic model validation with retry on failure.
- **Memory:** `remember`/`recall`, semantic memory search, optional vector storage.
- **Context management:** auto compaction when token budget is exceeded.
- **Declarative config / workspace:** YAML/JSON agent definitions and `.agentu/` directory loading (`Agent.from_workspace`).
- **Workflows:** agent chaining via `>>` (sequential) and `&` (parallel), with checkpoint/resume.
- **Caching:** exact, semantic, offline, and Redis-backed presets.
- **Observability:** OpenTelemetry GenAI spans, session manager, evaluation harness.
- **MCP transport:** recent STDIO transport for MCP servers.

## Our local AgentUnreal harness

A minimal, domain-specific harness at `/Users/noctis/Desktop/ue-agent-harness`. It is purpose-built to drive Unreal Engine C++ changes.

- **Domain focus:** scans `.uproject`/Build.cs, reads/writes source files, calls UBT, and drives the Unreal Editor via a file bridge.
- **ReAct loop:** simple `while` loop in `agent.py` with hard-coded `max_iterations=10`.
- **Bridge:** file-based handshake between the Python agent and a Python script running inside Unreal Editor; also supports a local `stub` mode for testing on macOS.
- **Tool registry:** hand-written `tools/` modules (`project`, `file`, `build`, `editor`, `bridge`).
- **Memory:** optional Mnemosyne integration (`memory_remember`/`memory_recall`).
- **LLM client:** supports Anthropic, OpenAI/OpenRouter, Ollama/LM Studio via `llm.py`.
- **Safety:** only a `dry_run` preview gate before `write_file` and a system-prompt rule to pair `.h`/`.cpp` files.
- **Tests:** a small `test_stub.py` self-check for stub bridge and dry-run.

## Architecture differences

| Dimension | `agentu` | AgentUnreal harness |
|-----------|----------|---------------------|
| Scope | General-purpose | Unreal Engine C++ workflow only |
| Tool sandboxing | Subprocess isolation with memory/network limits | None; tools run in-process |
| Permission model | READONLY/WRITE/DANGEROUS + approval modes | `dry_run` gate only |
| Config | Declarative YAML/JSON + `.agentu/` workspace | Single `config.yaml` + hard-coded logic |
| Tool definition | Auto-discovery from Python functions + docstrings | Manually wired in `agent.py` and `_tool_schemas` |
| Memory | Built-in, with semantic search | Optional Mnemosyne wrapper |
| Workflows | Built-in `>>`/`&` + checkpoint/resume | None |
| Caching | Multiple built-in cache presets | None |
| Guardrails | Output guardrails + self-correction | None beyond dry-run |
| Observability | OTel, sessions, evaluation | Session JSONL log + `progress.md` journal |
| LLM interface | Abstracted through its own providers | Direct Anthropic/OpenAI/OpenRouter/Ollama clients |

## Ideas AgentUnreal could borrow from `agentu`

1. **Subprocess tool sandboxing:** run `write_file`, `build_module`, and editor-side commands in isolated processes with timeout/memory limits. This would protect the host process from a hung UBT or a misbehaving editor script.
2. **Declarative workspace / YAML config:** adopt an `agent.yaml` that auto-discovers tools and system prompts, reducing the hard-wiring in `agent.py`.
3. **Auto tool discovery from `tools/`:** derive OpenAI-style tool schemas from function signatures and docstrings instead of maintaining `_tool_schemas()` by hand.
4. **Permission tags:** label tools as `READONLY`/`WRITE`/`DANGEROUS` and require explicit approval for destructive operations (e.g. deleting files, force-rebuilding).
5. **Guardrails + self-correction:** add simple output guardrails (e.g. reject edits outside the project tree, validate file paths) and retry on violation.
6. **Code mode:** for complex multi-step C++ edits, let the LLM generate a short Python script that calls the file/build tools rather than one tool call per turn.
7. **Workflow primitives:** if the harness later chains tasks (e.g. “scan → generate → build → hot-reload”), `>>`/`&` style composition with checkpointing is a clean pattern.
8. **Caching:** cache LLM responses for identical `dry_run` or project-scan prompts to reduce API costs.
9. **Context compaction:** implement a token-budget cap and truncation strategy so long sessions do not blow past context limits.
10. **Observability hooks:** add pre/post tool hooks for logging, rate-limiting, or auditing before/after UBT/editor calls.

## Bottom line

`agentu` is a mature, general-purpose runtime that solves many of the problems AgentUnreal would eventually face. Our harness is far smaller and domain-specific, which is appropriate for an early prototype. The highest-value borrow, in order, is: **subprocess tool isolation**, **declarative config + auto tool discovery**, **permission scoping**, and **guardrails/self-correction**.
