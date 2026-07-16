"""LLM client with Anthropic, OpenAI, and Ollama support."""

import os
import json
from typing import Any

from dotenv import load_dotenv

load_dotenv()  # ponytail: pull .env secrets into os.environ before client init


class LLM:
    def __init__(self, config: Any):
        self.provider = config.llm_provider.lower()
        self.model = config.llm_model
        self.api_key_env = config.llm_api_key_env
        self.base_url = config.llm_base_url or ""
        self.api_key = os.getenv(self.api_key_env) if self.api_key_env else ""

        self.client = None
        self._init_client()

    def _init_client(self) -> None:
        if self.provider in ("anthropic", "claude"):
            import anthropic
            self.client = anthropic.Anthropic(api_key=self.api_key)
        elif self.provider in ("openai", "openrouter"):
            import openai
            base = self.base_url or ("https://openrouter.ai/api/v1" if self.provider == "openrouter" else None)
            self.client = openai.OpenAI(api_key=self.api_key, base_url=base)
        elif self.provider in ("ollama", "local", "lmstudio"):
            import openai
            base = self.base_url or ("http://127.0.0.1:11434/v1" if self.provider == "ollama" else "http://127.0.0.1:1234/v1")
            self.client = openai.OpenAI(api_key=self.api_key or "not-needed", base_url=base)
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")

    def invoke(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        if self.provider in ("anthropic", "claude"):
            return self._anthropic(messages, tools)
        return self._openai_style(messages, tools)

    def _anthropic(self, messages: list[dict], tools: list[dict] | None) -> dict:
        system = ""
        cleaned = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                cleaned.append(m)
        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": cleaned,
            "system": system,
        }
        if tools:
            kwargs["tools"] = self._to_anthropic_tools(tools)
        resp = self.client.messages.create(**kwargs)
        return self._from_anthropic_response(resp)

    def _openai_style(self, messages: list[dict], tools: list[dict] | None) -> dict:
        kwargs = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        return self._from_openai_response(resp)

    def _to_anthropic_tools(self, tools: list[dict]) -> list[dict]:
        out = []
        for t in tools:
            fn = t.get("function", t)
            out.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object"}),
            })
        return out

    def _from_anthropic_response(self, resp) -> dict:
        content = ""
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    }
                })
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    def _from_openai_response(self, resp) -> dict:
        message = resp.choices[0].message
        content = message.content or ""
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                })
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}


def demo():
    """Self-check that client construction works with the current config."""
    from agent import Config
    config = Config(
        uproject_path="",
        default_module="",
        bridge_type="stub",
        bridge_file_path="",
        bridge_poll_interval=0.05,
        bridge_timeout=5.0,
        llm_provider="anthropic",
        llm_model="claude-sonnet-4",
        llm_api_key_env="ANTHROPIC_API_KEY",
        llm_base_url="",
        max_build_retries=3,
        memory_enabled=False,
        journal_path="progress.md",
        db_path="memory.db",
        approval_mode="auto",
        allowed_paths=[],
    )
    llm = LLM(config)
    print(f"provider={llm.provider} model={llm.model}")
    print(f"api_key_env set={bool(llm.api_key)}")
    print(f"base_url={llm.base_url or 'default'}")


if __name__ == "__main__":
    demo()
