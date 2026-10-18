#!/usr/bin/env python3
"""Stage 01 — be the client yourself.

Launches 01_raw/server.py as a subprocess (exactly what Claude Desktop or any
MCP host does with a stdio server) and sends it hand-written JSON-RPC messages,
printing everything that crosses the wire.

    python 01_raw/poke.py                    # talk to the hand-written server
    python 01_raw/poke.py uv run 02_sdk/server.py   # same messages, SDK server
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "poke", "version": "0.1.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

BLUE, GREEN, RED, DIM, RESET = "\033[34m", "\033[32m", "\033[31m", "\033[2m", "\033[0m"
_ids = itertools.count(1)


def request(proc: subprocess.Popen, method: str, params: dict | None = None, meta: dict | None = META) -> dict:
    params = dict(params or {})
    if meta is not None:
        params["_meta"] = meta
    msg = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params}
    print(f"\n{BLUE}──▶ {method}{RESET}\n{DIM}{json.dumps(msg, indent=2)}{RESET}")
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    reply = json.loads(proc.stdout.readline())
    colour = RED if "error" in reply else GREEN
    print(f"{colour}◀── reply{RESET}\n{json.dumps(reply, indent=2)}")
    return reply


def main() -> None:
    cmd = sys.argv[1:] or [sys.executable, str(HERE / "server.py")]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    # 1. Discovery: who are you, which versions and capabilities do you have?
    request(proc, "server/discover")
    # 2. The three primitives
    request(proc, "tools/list")
    request(proc, "tools/call", {"name": "convert_units", "arguments": {"value": 26.2, "from_unit": "mi", "to_unit": "km"}})
    request(proc, "resources/read", {"uri": "lab://units"})
    request(proc, "prompts/get", {"name": "plan_recipe_conversion", "arguments": {"recipe": "2 cups flour, 1 lb butter"}})
    # 3. The two kinds of failure
    request(proc, "tools/call", {"name": "calculate", "arguments": {"expression": "1/0"}})  # tool error -> isError
    request(proc, "tools/call", {"name": "nope", "arguments": {}})  # protocol error -> JSON-RPC error
    # 4. Statelessness in action: a request with no _meta is rejected, even mid-"session"
    request(proc, "tools/list", meta=None)
    # 5. Version negotiation
    request(proc, "tools/list", meta={**META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"})

    proc.stdin.close()
    proc.wait(timeout=5)


if __name__ == "__main__":
    main()
