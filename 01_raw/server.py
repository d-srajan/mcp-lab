#!/usr/bin/env python3
"""Stage 01 — an MCP server written by hand. No SDK, standard library only.

Run it and talk to it yourself:

    python 01_raw/server.py            # then paste JSON lines (see 01_raw/README.md)
    python 01_raw/poke.py              # scripted conversation, pretty-printed

What this file teaches
----------------------
* The **stdio transport** is just newline-delimited JSON. The client writes one
  JSON-RPC message per line to our stdin; we write one per line to stdout.
  stdout is reserved for protocol messages, so all logging goes to stderr.
* **JSON-RPC 2.0**: a request has an `id` and expects a response with the same
  `id`; a notification has no `id` and must never be answered.
* **The 2026-07-28 "modern" protocol is stateless.** There is no `initialize`
  handshake. Every request carries its protocol version and the client's
  capabilities in `params._meta`, and we validate them on every request.
* **The three server primitives**: tools (the model calls them), resources
  (the application reads them), prompts (the user picks them).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mcp_lab import toolbox  # noqa: E402

SUPPORTED_VERSIONS = ["2026-07-28"]
SERVER_INFO = {"name": "mcp-lab-raw", "version": "0.1.0"}

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# JSON-RPC / MCP error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_PROTOCOL_VERSION = -32022  # defined by the MCP spec


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def log(*args: Any) -> None:
    """Never print() to stdout in a stdio server; stdout belongs to the protocol."""
    print("[raw-server]", *args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# 1. The catalogue: what we advertise in tools/list, resources/list, prompts/list
# ---------------------------------------------------------------------------
# With the SDK (stage 02) these JSON Schemas are generated from type hints.
# Here we write them out by hand so you can see exactly what the LLM ends up seeing.

TOOLS: list[dict] = [
    {
        "name": "calculate",
        "title": "Calculator",
        "description": "Evaluate an arithmetic expression, e.g. '(2 + 3) * 4 ** 2'. "
        "Supports + - * / // % ** and parentheses.",
        "inputSchema": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "Arithmetic expression"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    },
    {
        "name": "roll_dice",
        "title": "Dice roller",
        "description": "Roll one or more dice and return each roll and the total.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sides": {"type": "integer", "minimum": 2, "maximum": 1000, "default": 6},
                "count": {"type": "integer", "minimum": 1, "maximum": 100, "default": 1},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "convert_units",
        "title": "Unit converter",
        "description": "Convert a value between units of length, mass, volume or temperature "
        "(see resource lab://units for supported units).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "from_unit": {"type": "string", "description": "e.g. 'mi', 'lb', 'f'"},
                "to_unit": {"type": "string", "description": "e.g. 'km', 'kg', 'c'"},
            },
            "required": ["value", "from_unit", "to_unit"],
            "additionalProperties": False,
        },
    },
]

TOOL_IMPLS: dict[str, Callable[..., Any]] = {
    "calculate": lambda expression: {"result": toolbox.calculate(expression)},
    "roll_dice": toolbox.roll_dice,
    "convert_units": toolbox.convert_units,
}

RESOURCES: list[dict] = [
    {"uri": "lab://about", "name": "about", "title": "About this server", "mimeType": "text/plain"},
    {"uri": "lab://units", "name": "units", "title": "Supported units", "mimeType": "text/markdown"},
]
RESOURCE_READERS: dict[str, Callable[[], str]] = {
    "lab://about": lambda: toolbox.ABOUT,
    "lab://units": toolbox.units_reference,
}

PROMPTS: list[dict] = [
    {
        "name": "plan_recipe_conversion",
        "title": "Convert a recipe to metric",
        "description": "Ask the assistant to convert every quantity in a recipe using convert_units.",
        "arguments": [
            {"name": "recipe", "description": "The recipe text", "required": True},
            {"name": "target_system", "description": "'metric' (default) or 'imperial'", "required": False},
        ],
    }
]


# ---------------------------------------------------------------------------
# 2. Method handlers. Each takes `params` and returns a `result` dict.
# ---------------------------------------------------------------------------


def complete(result: dict) -> dict:
    """Every modern result carries resultType, and SHOULD identify the server."""
    return {"resultType": "complete", **result, "_meta": {META_SERVER_INFO: SERVER_INFO}}


def cacheable(result: dict, ttl_ms: int = 60_000) -> dict:
    """discover, */list and resources/read are *cacheable* in 2026-07-28: the result MUST say
    how long the client may cache it (like HTTP Cache-Control). Strict clients such as the
    official SDK reject a list result without these two fields. Try deleting them and
    running 03_client/client.py to see that happen."""
    return {**complete(result), "ttlMs": ttl_ms, "cacheScope": "public"}


def handle_discover(params: dict) -> dict:
    return {
        "resultType": "complete",
        "supportedVersions": SUPPORTED_VERSIONS,
        "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
        "instructions": "Toy toolbox. Prefer tools over mental arithmetic.",
        "_meta": {META_SERVER_INFO: SERVER_INFO},
        "ttlMs": 3_600_000,
        "cacheScope": "public",
    }


def handle_tools_list(params: dict) -> dict:
    return cacheable({"tools": TOOLS})


def handle_tools_call(params: dict) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    if name not in TOOL_IMPLS:
        # Unknown tool = protocol error (the request itself is wrong)...
        raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
    try:
        value = TOOL_IMPLS[name](**args)
    except (ValueError, TypeError) as e:
        # ...but a tool that fails = a *result* with isError, so the LLM can read the
        # message and try again with better arguments.
        return complete({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
    return complete(
        {
            # `content` is for the model; `structuredContent` is for programs.
            "content": [{"type": "text", "text": json.dumps(value)}],
            "structuredContent": value,
            "isError": False,
        }
    )


def handle_resources_list(params: dict) -> dict:
    return cacheable({"resources": RESOURCES})


def handle_resources_read(params: dict) -> dict:
    uri = params.get("uri")
    if uri not in RESOURCE_READERS:
        raise RpcError(INVALID_PARAMS, f"Resource not found: {uri}", {"uri": uri})
    mime = next(r["mimeType"] for r in RESOURCES if r["uri"] == uri)
    return cacheable({"contents": [{"uri": uri, "mimeType": mime, "text": RESOURCE_READERS[uri]()}]}, ttl_ms=0)


def handle_prompts_list(params: dict) -> dict:
    return cacheable({"prompts": PROMPTS})


def handle_prompts_get(params: dict) -> dict:
    if params.get("name") != "plan_recipe_conversion":
        raise RpcError(INVALID_PARAMS, f"Unknown prompt: {params.get('name')}")
    args = params.get("arguments") or {}
    if "recipe" not in args:
        raise RpcError(INVALID_PARAMS, "Missing required argument: recipe")
    text = toolbox.recipe_prompt(args["recipe"], args.get("target_system", "metric"))
    return complete(
        {
            "description": "Recipe conversion",
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
    )


HANDLERS: dict[str, Callable[[dict], dict]] = {
    "server/discover": handle_discover,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
    "resources/list": handle_resources_list,
    "resources/read": handle_resources_read,
    "prompts/list": handle_prompts_list,
    "prompts/get": handle_prompts_get,
}


# ---------------------------------------------------------------------------
# 3. The dispatcher: JSON-RPC envelope + per-request protocol checks
# ---------------------------------------------------------------------------


def check_meta(params: dict) -> None:
    """Stateless protocol: every request must say which version it speaks."""
    meta = params.get("_meta") or {}
    if META_VERSION not in meta or META_CLIENT_CAPS not in meta:
        raise RpcError(
            INVALID_PARAMS,
            f"Request _meta must include {META_VERSION!r} and {META_CLIENT_CAPS!r}",
        )
    if meta[META_VERSION] not in SUPPORTED_VERSIONS:
        raise RpcError(
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": SUPPORTED_VERSIONS, "requested": meta[META_VERSION]},
        )
    client = meta.get(META_CLIENT_INFO, {})
    log(f"  client={client.get('name', '?')} version={meta[META_VERSION]}")


def handle_message(msg: Any) -> dict | None:
    """Return the response to send, or None for notifications."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": "Invalid Request"}}

    method, params = msg["method"], msg.get("params") or {}
    is_notification = "id" not in msg
    log(f"<- {method}" + (" (notification)" if is_notification else f" id={msg['id']}"))
    if is_notification:
        return None  # e.g. notifications/cancelled; nothing we need to do

    req_id = msg["id"]
    try:
        if method == "initialize":
            # A legacy (2025-11-25 or older) client. The spec says a modern-only server
            # SHOULD tell it which versions we do speak, since it has no other way to find out.
            raise RpcError(
                METHOD_NOT_FOUND,
                f"This server only speaks MCP {', '.join(SUPPORTED_VERSIONS)} (no initialize handshake)",
                {"supported": SUPPORTED_VERSIONS},
            )
        if method not in HANDLERS:
            raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
        check_meta(params)
        result = HANDLERS[method](params)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except RpcError as e:
        err = {"code": e.code, "message": e.message}
        if e.data is not None:
            err["data"] = e.data
        return {"jsonrpc": "2.0", "id": req_id, "error": err}
    except Exception as e:  # a bug in our server, not the client's fault
        log(f"internal error: {e!r}")
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": INTERNAL_ERROR, "message": str(e)}}


def main() -> None:
    log(f"ready, speaking MCP {SUPPORTED_VERSIONS} over stdio")
    for line in sys.stdin:  # one JSON-RPC message per line
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            response: dict | None = {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "Parse error"}}
        else:
            response = handle_message(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()  # forgetting this is the #1 bug in hand-rolled stdio servers
    log("stdin closed, exiting")


if __name__ == "__main__":
    main()
