"""Stage 04 — a personal AI agent that uses MCP servers as its toolbelt.

    uv run 04_agent/agent.py                           # interactive REPL
    uv run 04_agent/agent.py "what's 26.2 miles in km, and roll 2d20"
    uv run 04_agent/agent.py --confirm                 # approve each tool call
    uv run 04_agent/agent.py --config my_servers.json

Any LLM with function calling works, because the agent translates between the
two worlds:

    MCP tool (name, description, inputSchema)  ──▶  OpenAI "function" tool
    model's tool_call(name, arguments)         ──▶  MCP tools/call
    MCP CallToolResult (content, isError)      ──▶  "tool" message for the model

The agent here plays the role of the MCP **host**: it owns the LLM
conversation, runs one MCP **client** per configured server, and decides
how each primitive reaches the model:

* tools     → offered to the model directly (model-controlled)
* resources → listed in the system prompt; the model can ask for one through a
              built-in `read_resource` tool, and you can attach one with /read
              (application-controlled)
* prompts   → slash commands for *you*: /prompt <server> <name> key=value ...
              (user-controlled)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import Client, StdioServerParameters
from mcp_types import TextContent

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).resolve().parent / "servers.json"
MAX_STEPS = 8  # tool-use rounds per user turn, so a confused model can't loop forever

load_dotenv(ROOT / ".env")

DIM, CYAN, YELLOW, RED, RESET = "\033[2m", "\033[36m", "\033[33m", "\033[31m", "\033[0m"


# ---------------------------------------------------------------------------
# The MCP side: connect to every server and keep a combined catalogue
# ---------------------------------------------------------------------------


@dataclass
class ToolRef:
    server: str
    name: str  # the tool's real name on its server


@dataclass
class Host:
    """Holds one MCP client per server, plus the merged tool/resource/prompt catalogue."""

    clients: dict[str, Client] = field(default_factory=dict)
    tools: dict[str, ToolRef] = field(default_factory=dict)  # llm-facing name -> where it lives
    llm_tools: list[dict] = field(default_factory=list)  # OpenAI-format tool definitions
    resources: dict[str, list[Any]] = field(default_factory=dict)
    prompts: dict[str, list[Any]] = field(default_factory=dict)
    instructions: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def make_client(spec: dict) -> Client:
        if "url" in spec:  # Streamable HTTP (stage 05, or any remote server)
            return Client(spec["url"])
        command = sys.executable if spec["command"] == "python" else spec["command"]
        return Client(
            StdioServerParameters(
                command=command,
                args=spec.get("args", []),
                # The SDK passes servers only a minimal safe environment (PATH, HOME...),
                # so your OPENROUTER_API_KEY doesn't leak into third-party servers.
                # Give a server a secret explicitly via "env" in servers.json.
                env=spec.get("env"),
                cwd=str(ROOT),
            )
        )

    async def connect(self, stack: AsyncExitStack, config: dict) -> None:
        for name, spec in config["servers"].items():
            if not spec.get("enabled", True):
                continue
            try:
                client = await stack.enter_async_context(self.make_client(spec))
            except Exception as e:  # one broken server shouldn't take the agent down
                print(f"{RED}! could not start server {name!r}: {e}{RESET}")
                continue
            self.clients[name] = client
            if client.instructions:  # from server/discover
                self.instructions[name] = client.instructions
            await self.refresh(name)

        self.llm_tools.append(READ_RESOURCE_TOOL)

    async def refresh(self, server: str) -> None:
        client = self.clients[server]
        for tool in (await client.list_tools()).tools:
            # Two servers may both have a "search" tool, so namespace them.
            # OpenAI-style function names only allow [A-Za-z0-9_-], max 64 chars.
            llm_name = re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}__{tool.name}")[:64]
            self.tools[llm_name] = ToolRef(server, tool.name)
            self.llm_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": llm_name,
                        "description": tool.description or tool.title or tool.name,
                        "parameters": tool.input_schema,
                    },
                }
            )
        # Not every server implements every primitive, so these lists are best-effort.
        try:
            self.resources[server] = (await client.list_resources()).resources
        except Exception:
            self.resources[server] = []
        try:
            self.prompts[server] = (await client.list_prompts()).prompts
        except Exception:
            self.prompts[server] = []

    async def call_tool(self, llm_name: str, args: dict) -> tuple[str, bool]:
        if llm_name == READ_RESOURCE_TOOL["function"]["name"]:
            return await self.read_resource(args.get("server", ""), args.get("uri", ""))
        ref = self.tools.get(llm_name)
        if ref is None:
            return f"Unknown tool {llm_name}", True
        result = await self.clients[ref.server].call_tool(ref.name, args)
        text = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
        return text or json.dumps(result.structured_content), bool(result.is_error)

    async def read_resource(self, server: str, uri: str) -> tuple[str, bool]:
        if server not in self.clients:
            return f"Unknown server {server!r}. Known: {list(self.clients)}", True
        try:
            res = await self.clients[server].read_resource(uri)
        except Exception as e:
            return f"Could not read {uri}: {e}", True
        return "\n".join(getattr(c, "text", f"<{c.mime_type} blob>") for c in res.contents), False

    def system_prompt(self) -> str:
        lines = [
            "You are a helpful personal assistant with tools provided by MCP servers.",
            "Use tools for anything they can do (arithmetic, conversions, lookups) instead of guessing.",
            "Keep answers short.",
        ]
        for server, text in self.instructions.items():
            lines.append(f"Server {server!r} says: {text}")
        catalogue = [f"- server={s!r} uri={r.uri!r}: {r.title or r.name}" for s, rs in self.resources.items() for r in rs]
        if catalogue:
            lines.append("Resources you can fetch with read_resource:\n" + "\n".join(catalogue))
        return "\n".join(lines)


READ_RESOURCE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_resource",
        "description": "Read an MCP resource (reference data such as docs or files) by server name and URI.",
        "parameters": {
            "type": "object",
            "properties": {"server": {"type": "string"}, "uri": {"type": "string"}},
            "required": ["server", "uri"],
        },
    },
}


# ---------------------------------------------------------------------------
# The LLM side: a plain tool-calling loop against any OpenAI-compatible API
# ---------------------------------------------------------------------------


def make_llm():
    from openai import AsyncOpenAI

    key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        sys.exit("Set OPENROUTER_API_KEY in .env (copy .env.example). Get one at https://openrouter.ai/keys")
    base_url = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    return AsyncOpenAI(api_key=key, base_url=base_url)


class Agent:
    def __init__(self, host: Host, llm, model: str, confirm: bool = False):
        self.host, self.llm, self.model, self.confirm = host, llm, model, confirm
        self.messages: list[dict] = [{"role": "system", "content": host.system_prompt()}]

    async def ask(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        for _ in range(MAX_STEPS):
            resp = await self.llm.chat.completions.create(
                model=self.model, messages=self.messages, tools=self.host.llm_tools
            )
            msg = resp.choices[0].message
            self.messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                return msg.content or ""

            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                print(f"{CYAN}  ⚙ {call.function.name}({json.dumps(args)}){RESET}")
                if self.confirm and input(f"{YELLOW}    allow? [Y/n] {RESET}").strip().lower() == "n":
                    output, is_error = "The user declined this tool call.", True
                else:
                    output, is_error = await self.host.call_tool(call.function.name, args)
                colour = RED if is_error else DIM
                print(f"{colour}    → {output[:200]}{RESET}")
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
        return "(stopped: too many tool calls in a row)"


# ---------------------------------------------------------------------------
# REPL with slash commands for the user-controlled primitives
# ---------------------------------------------------------------------------

HELP = """commands:
  /tools                              list tools the model can call
  /resources                          list resources
  /read <server> <uri>                attach a resource to the conversation
  /prompts                            list prompt templates
  /prompt <server> <name> k=v ...     run a prompt template (quote values with spaces)
  /quit"""


async def handle_command(agent: Agent, line: str) -> str | None:
    """Returns text to send to the model, or None if the command was handled locally."""
    import shlex

    parts = shlex.split(line)
    cmd, args = parts[0], parts[1:]
    host = agent.host
    if cmd == "/tools":
        for name, ref in host.tools.items():
            print(f"  {name}  ({ref.server}:{ref.name})")
    elif cmd == "/resources":
        for s, rs in host.resources.items():
            for r in rs:
                print(f"  {s}  {r.uri}  {r.title or r.name}")
    elif cmd == "/read" and len(args) == 2:
        text, err = await host.read_resource(*args)
        print(f"{DIM}{text}{RESET}")
        if not err:
            agent.messages.append({"role": "user", "content": f"[Attached resource {args[1]}]\n{text}"})
            print("  (attached to the conversation)")
    elif cmd == "/prompts":
        for s, ps in host.prompts.items():
            for p in ps:
                print(f"  {s}  {p.name}({', '.join(a.name for a in p.arguments or [])})  {p.description or ''}")
    elif cmd == "/prompt" and len(args) >= 2:
        server, name = args[0], args[1]
        kv = dict(a.split("=", 1) for a in args[2:] if "=" in a)
        result = await host.clients[server].get_prompt(name, kv)
        text = "\n".join(m.content.text for m in result.messages if hasattr(m.content, "text"))
        print(f"{DIM}  prompt expands to:\n  {text}{RESET}")
        return text
    else:
        print(HELP)
    return None


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="*", help="ask one question and exit")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--model", default=os.getenv("LLM_MODEL", "google/gemini-3.8-flash"))
    ap.add_argument("--confirm", action="store_true", help="ask before every tool call")
    opts = ap.parse_args()

    config = json.loads(opts.config.read_text())
    async with AsyncExitStack() as stack:
        host = Host()
        await host.connect(stack, config)
        print(f"{DIM}model={opts.model}  servers={list(host.clients)}  tools={len(host.tools)}{RESET}")
        agent = Agent(host, make_llm(), opts.model, opts.confirm)

        if opts.question:
            print(await agent.ask(" ".join(opts.question)))
            return

        print("Type a question, /help for commands, /quit to exit.")
        while True:
            try:
                line = (await asyncio.to_thread(input, "\nyou> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            try:
                if line.startswith("/"):
                    line = await handle_command(agent, line)
                    if line is None:
                        continue
                print(f"\nagent> {await agent.ask(line)}")
            except Exception as e:
                print(f"{RED}error: {e}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
