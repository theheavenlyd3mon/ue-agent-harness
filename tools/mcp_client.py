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
        self.tools = discovered
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
