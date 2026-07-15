"""
Client that simulates how an Agent discovers and calls remote HTTP tools.

Run while calculator_http_tool_server.py is running:
    python examples/calculator_http_tool_client.py --base-url http://127.0.0.1:8766
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from typing import Any


def get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover and call calculator HTTP tools.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    base_url = args.base_url.rstrip("/")

    print("1. Discover remote tools")
    tools_response = get_json(f"{base_url}/tools")
    print(json.dumps(tools_response, ensure_ascii=False, indent=2))

    print("\n2. Call multiply(a=12.5, b=8)")
    call_response = post_json(
        f"{base_url}/call",
        {
            "name": "multiply",
            "arguments": {"a": 12.5, "b": 8},
        },
    )
    print(json.dumps(call_response, ensure_ascii=False, indent=2))

    print("\n3. Call divide(a=10, b=4)")
    call_response = post_json(
        f"{base_url}/call",
        {
            "name": "divide",
            "arguments": {"a": 10, "b": 4},
        },
    )
    print(json.dumps(call_response, ensure_ascii=False, indent=2))
