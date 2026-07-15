"""
A tiny HTTP tool server that simulates MCP-style tool discovery and calls.

This is intentionally minimal and educational.  It is not a complete MCP
implementation, but it mirrors the two key ideas:

1. The client asks the server for available tools: "tools/list".
2. The client calls one named tool with JSON arguments: "tools/call".

Run:
    python examples/calculator_http_tool_server.py --host 127.0.0.1 --port 8766
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


ToolFunc = Callable[[dict[str, Any]], Any]


def add(args: dict[str, Any]) -> float:
    return float(args["a"]) + float(args["b"])


def subtract(args: dict[str, Any]) -> float:
    return float(args["a"]) - float(args["b"])


def multiply(args: dict[str, Any]) -> float:
    return float(args["a"]) * float(args["b"])


def divide(args: dict[str, Any]) -> float:
    b = float(args["b"])
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return float(args["a"]) / b


TOOLS: dict[str, dict[str, Any]] = {
    "add": {
        "description": "Return a + b.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
        "func": add,
    },
    "subtract": {
        "description": "Return a - b.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
        "func": subtract,
    },
    "multiply": {
        "description": "Return a * b.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
        "func": multiply,
    },
    "divide": {
        "description": "Return a / b. Fails when b is zero.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
        "func": divide,
    },
}


def list_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
        }
        for name, spec in TOOLS.items()
    ]


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name not in TOOLS:
        raise ValueError(f"Unknown tool: {name}")
    return TOOLS[name]["func"](arguments)


class CalculatorToolHandler(BaseHTTPRequestHandler):
    server_version = "CalculatorHTTPToolServer/0.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"ok": True})
            return
        if self.path == "/tools":
            self._send_json({"tools": list_tools()})
            return
        self._send_json({"error": f"Unknown path: {self.path}"}, status=404)

    def do_POST(self) -> None:
        if self.path != "/call":
            self._send_json({"error": f"Unknown path: {self.path}"}, status=404)
            return

        try:
            request = self._read_json()
            name = request["name"]
            arguments = request.get("arguments", {})
            result = call_tool(name, arguments)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({"result": result})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.client_address[0]} - {format % args}")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a calculator HTTP tool server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), CalculatorToolHandler)
    print(f"Calculator HTTP tool server listening on http://{args.host}:{args.port}")
    print("GET  /tools                    discover tools")
    print("POST /call {name, arguments}   call a tool")
    server.serve_forever()
