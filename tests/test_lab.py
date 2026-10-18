"""Tests for every stage. No API key needed: the agent test uses a scripted fake LLM.

    uv run pytest -q
"""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp_types import TextContent
from openai.types.chat import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall, Function

from mcp_lab import toolbox

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def text(result) -> str:
    return "".join(b.text for b in result.content if isinstance(b, TextContent))


# --- toolbox ---------------------------------------------------------------


def test_toolbox():
    assert toolbox.calculate("(2 + 3) * 4 ** 2") == 80
    with pytest.raises(ValueError):
        toolbox.calculate("__import__('os')")
    assert toolbox.convert_units(212, "F", "C")["value"] == 100
    assert toolbox.convert_units(1, "mi", "km")["value"] == pytest.approx(1.609344)
    with pytest.raises(ValueError):
        toolbox.convert_units(1, "kg", "km")
    r = toolbox.roll_dice(6, 10)
    assert len(r["rolls"]) == 10 and all(1 <= x <= 6 for x in r["rolls"])


# --- stage 01: raw protocol, spoken by hand --------------------------------


def raw_exchange(*messages: dict) -> list[dict]:
    lines = "\n".join(json.dumps(m) for m in messages) + "\n"
    out = subprocess.run(
        [sys.executable, str(ROOT / "01_raw/server.py")], input=lines, capture_output=True, text=True, timeout=10
    ).stdout
    return [json.loads(line) for line in out.splitlines()]


META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


def test_raw_server_wire_format():
    replies = raw_exchange(
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": META}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}},  # no reply expected
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},  # missing _meta
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {"_meta": {**META, "io.modelcontextprotocol/protocolVersion": "2024-11-05"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {}},
    )
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert replies[0]["result"]["supportedVersions"] == ["2026-07-28"]
    assert replies[1]["error"]["code"] == -32602
    assert replies[2]["error"]["code"] == -32022
    assert replies[3]["error"]["data"]["supported"] == ["2026-07-28"]


async def test_sdk_client_talks_to_raw_server():
    """The official client accepts our hand-written server: proof we implemented the protocol."""
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "01_raw/server.py")])
    async with Client(params) as client:
        assert {t.name for t in (await client.list_tools()).tools} == {"calculate", "roll_dice", "convert_units"}
        res = await client.call_tool("calculate", {"expression": "6*7"})
        assert res.structured_content == {"result": 42}
        bad = await client.call_tool("calculate", {"expression": "1/0"})
        assert bad.is_error and "division by zero" in text(bad)
        assert "length" in (await client.read_resource("lab://units")).contents[0].text
        prompt = await client.get_prompt("plan_recipe_conversion", {"recipe": "1 cup milk"})
        assert "1 cup milk" in prompt.messages[0].content.text


# --- stage 02: SDK server (connected in-process, no subprocess) ------------


def sdk_server():
    return runpy.run_path(str(ROOT / "02_sdk/server.py"), run_name="test")["mcp"]


async def test_sdk_server():
    async with Client(sdk_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert tools["roll_dice"].input_schema["properties"]["sides"]["maximum"] == 1000
        res = await client.call_tool("convert_units", {"value": 0, "from_unit": "c", "to_unit": "k"})
        assert res.structured_content["value"] == 273.15
        bad = await client.call_tool("convert_units", {"value": 1, "from_unit": "kg", "to_unit": "km"})
        assert bad.is_error and "can't convert" in text(bad)  # ToolError message reaches the model
        templ = await client.read_resource("lab://units/volume")
        assert "gal" in templ.contents[0].text


# --- stage 04: agent loop with a fake LLM ----------------------------------


class FakeLLM:
    """Plays back a script of assistant messages and records what it was sent."""

    def __init__(self, script: list[ChatCompletionMessage]):
        self.script, self.calls = list(script), []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        msg = self.script.pop(0)
        return type("Resp", (), {"choices": [type("Choice", (), {"message": msg})()]})()


async def test_agent_routes_tool_calls_through_mcp():
    agent_mod = runpy.run_path(str(ROOT / "04_agent/agent.py"), run_name="test")
    Host, Agent = agent_mod["Host"], agent_mod["Agent"]
    config = {"servers": {"toolbox": {"command": "python", "args": ["02_sdk/server.py"]}}}

    tool_call = ChatCompletionMessageToolCall(
        id="call_1",
        type="function",
        function=Function(name="toolbox__convert_units", arguments='{"value": 26.2, "from_unit": "mi", "to_unit": "km"}'),
    )
    llm = FakeLLM(
        [
            ChatCompletionMessage(role="assistant", content=None, tool_calls=[tool_call]),
            ChatCompletionMessage(role="assistant", content="About 42.2 km."),
        ]
    )
    async with AsyncExitStack() as stack:
        host = Host()
        await host.connect(stack, config)
        assert "toolbox__calculate" in host.tools
        assert "lab://units" in host.system_prompt()
        agent = Agent(host, llm, model="fake")
        answer = await agent.ask("How far is a marathon in km?")

    assert answer == "About 42.2 km."
    sent_tools = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    assert {"toolbox__convert_units", "read_resource"} <= sent_tools
    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and "42.164813" in tool_msgs[0]["content"]  # the MCP result reached the model
