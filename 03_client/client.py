"""Stage 03 — an MCP client with the official SDK (no LLM yet).

    uv run 03_client/client.py                     # talks to the hand-written server (01)
    uv run 03_client/client.py 02_sdk/server.py    # talks to the SDK server (02)
    uv run 03_client/client.py --spy               # same, but log every wire message

The interesting part: the SDK client works against *your hand-written server*
from stage 01. That's the promise of a protocol. Any compliant client can talk
to any compliant server, whoever wrote them and in whatever language.

This is the half of MCP a "host" (Claude Desktop, Claude Code, your own
agent) implements: launch the server, discover what it offers, and call it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp_types import TextContent

ROOT = Path(__file__).resolve().parents[1]


def server_params(script: str, spy: bool) -> StdioServerParameters:
    args = [str(ROOT / script)]
    if spy:  # put the wiretap proxy between us and the server
        args = [str(ROOT / "03_client" / "spy.py"), "--", sys.executable, *args]
    return StdioServerParameters(command=sys.executable, args=args)


def text_of(blocks) -> str:
    return "\n".join(b.text for b in blocks if isinstance(b, TextContent))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("server", nargs="?", default="01_raw/server.py", help="server script, relative to repo root")
    ap.add_argument("--spy", action="store_true", help="log raw JSON-RPC traffic to stderr and wire.log")
    opts = ap.parse_args()

    # `async with` is the whole connection lifecycle: it spawns the subprocess,
    # and on exit closes stdin so the server shuts down.
    async with Client(server_params(opts.server, opts.spy)) as client:
        print(f"Connected to {opts.server}\n")

        print("== tools (model-controlled) ==")
        tools = await client.list_tools()
        for t in tools.tools:
            print(f"  {t.name}: {t.description}")
            print(f"    input schema: {json.dumps(t.input_schema)}")

        print("\n== resources (application-controlled) ==")
        for r in (await client.list_resources()).resources:
            print(f"  {r.uri}  ({r.mime_type})  {r.title or r.name}")
        units = await client.read_resource("lab://units")
        print("  lab://units says:\n    " + units.contents[0].text.replace("\n", "\n    "))

        print("\n== prompts (user-controlled) ==")
        for p in (await client.list_prompts()).prompts:
            print(f"  {p.name}({', '.join(a.name for a in p.arguments or [])}): {p.description}")
        prompt = await client.get_prompt("plan_recipe_conversion", {"recipe": "1 cup sugar"})
        print(f"  rendered -> {prompt.messages[0].role}: {prompt.messages[0].content.text[:70]}...")

        print("\n== calling tools ==")
        for name, args in [
            ("calculate", {"expression": "(2 + 3) * 4 ** 2"}),
            ("roll_dice", {"sides": 20, "count": 3}),
            ("convert_units", {"value": 72, "from_unit": "f", "to_unit": "c"}),
            ("convert_units", {"value": 1, "from_unit": "parsec", "to_unit": "m"}),  # tool error
        ]:
            result = await client.call_tool(name, args)
            flag = "ERROR" if result.is_error else "ok"
            print(f"  {name}({args}) -> [{flag}] {text_of(result.content)[:80]!r}")
            if result.structured_content is not None:
                print(f"      structured: {result.structured_content}")


if __name__ == "__main__":
    asyncio.run(main())
