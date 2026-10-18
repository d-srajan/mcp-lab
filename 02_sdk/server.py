"""Stage 02 — the same server, rebuilt with the official Python SDK (mcp >= 2.0).

    uv run 02_sdk/server.py               # stdio (what hosts launch)
    uv run mcp dev 02_sdk/server.py       # optional: open in the MCP Inspector (needs Node)

Compare this file with 01_raw/server.py. Everything we hand-wrote there is
still happening; the SDK just does it for us:

* JSON Schemas are generated from the type hints and docstrings,
* the `_meta` version check, `server/discover` and error envelopes are built in,
* an exception inside a tool becomes `isError: true` automatically (raise
  `ToolError` when you want the model to see the message),
* the return value becomes both `content` (text for the model) and
  `structuredContent` (typed data for programs), because the return type is
  annotated.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from mcp_lab import toolbox

mcp = MCPServer(
    name="mcp-lab-sdk",
    version="0.1.0",
    instructions="Toy toolbox. Prefer tools over mental arithmetic.",
)


# ---------------------------------------------------------------------------
# Tools: model-controlled. The LLM decides when to call them.
# ---------------------------------------------------------------------------


class CalcResult(TypedDict):
    result: float


class DiceResult(TypedDict):
    rolls: list[int]
    total: int


class Conversion(TypedDict):
    value: float
    unit: str
    dimension: str


@mcp.tool(title="Calculator")
def calculate(expression: Annotated[str, Field(description="Arithmetic expression, e.g. '(2 + 3) * 4 ** 2'")]) -> CalcResult:
    """Evaluate an arithmetic expression. Supports + - * / // % ** and parentheses."""
    try:
        return {"result": toolbox.calculate(expression)}
    except ValueError as e:
        # Any other exception would reach the model only as "Error executing tool calculate".
        # ToolError passes our message through, so the model can fix its input and retry.
        raise ToolError(str(e)) from e


@mcp.tool(title="Dice roller")
def roll_dice(
    sides: Annotated[int, Field(ge=2, le=1000)] = 6,
    count: Annotated[int, Field(ge=1, le=100)] = 1,
) -> DiceResult:
    """Roll one or more dice and return each roll and the total."""
    try:
        return toolbox.roll_dice(sides, count)  # type: ignore[return-value]
    except ValueError as e:
        raise ToolError(str(e)) from e


@mcp.tool(title="Unit converter")
def convert_units(
    value: float,
    from_unit: Annotated[str, Field(description="e.g. 'mi', 'lb', 'f'")],
    to_unit: Annotated[str, Field(description="e.g. 'km', 'kg', 'c'")],
) -> Conversion:
    """Convert a value between units of length, mass, volume or temperature
    (see resource lab://units for supported units)."""
    try:
        return toolbox.convert_units(value, from_unit, to_unit)  # type: ignore[return-value]
    except ValueError as e:
        raise ToolError(str(e)) from e


# ---------------------------------------------------------------------------
# Resources: application-controlled. The host decides what to read and when.
# ---------------------------------------------------------------------------


@mcp.resource("lab://about", title="About this server", mime_type="text/plain")
def about() -> str:
    return toolbox.ABOUT


@mcp.resource("lab://units", title="Supported units", mime_type="text/markdown")
def units() -> str:
    return toolbox.units_reference()


@mcp.resource("lab://units/{dimension}", title="Units for one dimension", mime_type="text/plain")
def units_for(dimension: str) -> str:
    """A resource *template*: the client fills in {dimension}. Stage 01 doesn't have one."""
    if dimension == "temperature":
        return ", ".join(toolbox.TEMPERATURE_UNITS)
    if dimension not in toolbox.UNITS:
        raise ValueError(f"unknown dimension {dimension!r}")
    return ", ".join(toolbox.UNITS[dimension])


# ---------------------------------------------------------------------------
# Prompts: user-controlled. Reusable templates a user picks (e.g. a slash command).
# ---------------------------------------------------------------------------


@mcp.prompt(title="Convert a recipe to metric")
def plan_recipe_conversion(recipe: str, target_system: str = "metric") -> str:
    """Ask the assistant to convert every quantity in a recipe using convert_units."""
    return toolbox.recipe_prompt(recipe, target_system)


if __name__ == "__main__":
    mcp.run()  # stdio by default
