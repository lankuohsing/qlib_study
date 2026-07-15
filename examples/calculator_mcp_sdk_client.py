"""
MCP SDK client that discovers and calls the calculator MCP server.

Run while calculator_mcp_sdk_server.py is running:
    python examples/calculator_mcp_sdk_client.py --url http://127.0.0.1:8767/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value


async def run_client(url: str) -> None:
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            print("1. Discover remote MCP tools")
            tools_result = await session.list_tools()
            tools_summary = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema,
                }
                for tool in tools_result.tools
            ]
            print(json.dumps(to_jsonable(tools_summary), ensure_ascii=False, indent=2))

            print("\n2. Call multiply(a=12.5, b=8)")
            result = await session.call_tool("multiply", {"a": 12.5, "b": 8})
            print(json.dumps(to_jsonable(result.content), ensure_ascii=False, indent=2))

            print("\n3. Call divide(a=10, b=4)")
            result = await session.call_tool("divide", {"a": 10, "b": 4})
            print(json.dumps(to_jsonable(result.content), ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover and call calculator MCP SDK tools.")
    parser.add_argument("--url", default="http://127.0.0.1:8767/mcp")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_client(args.url))
