"""
Calculator MCP server implemented with the official Python MCP SDK.

This is closer to a real-world MCP service than calculator_http_tool_server.py:
the SDK handles tool registration, JSON schema generation, protocol handshake,
tool discovery, and tool calls.

Run:
    python examples/calculator_mcp_sdk_server.py --host 127.0.0.1 --port 8767

MCP endpoint:
    http://127.0.0.1:8767/mcp
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP


def create_server(host: str, port: int) -> FastMCP:
    mcp = FastMCP(
        "calculator-sdk",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
    )

    @mcp.tool()
    def add(a: float, b: float) -> float:
        """Return a + b."""
        return a + b

    @mcp.tool()
    def subtract(a: float, b: float) -> float:
        """Return a - b."""
        return a - b

    @mcp.tool()
    def multiply(a: float, b: float) -> float:
        """Return a * b."""
        return a * b

    @mcp.tool()
    def divide(a: float, b: float) -> float:
        """Return a / b. Raises an error when b is zero."""
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b

    return mcp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a calculator MCP SDK server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    server = create_server(args.host, args.port)
    print(f"Calculator MCP SDK server listening on http://{args.host}:{args.port}/mcp")
    server.run(transport="streamable-http")
