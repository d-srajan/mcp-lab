"""The toy "business logic" every stage of the lab exposes.

This file has nothing to do with MCP. It is plain Python, and that's the
point: an MCP server is just a thin protocol layer wrapped around functions
like these. Stage 01 wraps them by hand; stage 02 uses the SDK to do the
same thing in a fraction of the code.

Only the standard library is used, so the hand-written server in stage 01
stays dependency-free.
"""

from __future__ import annotations

import ast
import operator
import random

# ---------------------------------------------------------------------------
# calculate: a safe arithmetic evaluator (never eval() model input!)
# ---------------------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def calculate(expression: str) -> float:
    """Evaluate an arithmetic expression like "(2 + 3) * 4 ** 2".

    Raises ValueError on anything that isn't plain arithmetic.
    """

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("exponent too large")
            return _BIN_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"unsupported syntax: {ast.dump(node)[:60]}")

    if len(expression) > 200:
        raise ValueError("expression too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"not a valid expression: {e.msg}") from e
    try:
        return _eval(tree)
    except ZeroDivisionError as e:
        raise ValueError("division by zero") from e


# ---------------------------------------------------------------------------
# roll_dice
# ---------------------------------------------------------------------------


def roll_dice(sides: int = 6, count: int = 1) -> dict:
    """Roll `count` dice with `sides` faces. Returns the rolls and their total."""
    if not 2 <= sides <= 1000:
        raise ValueError("sides must be between 2 and 1000")
    if not 1 <= count <= 100:
        raise ValueError("count must be between 1 and 100")
    rolls = [random.randint(1, sides) for _ in range(count)]
    return {"rolls": rolls, "total": sum(rolls)}


# ---------------------------------------------------------------------------
# convert_units
# ---------------------------------------------------------------------------

# Every unit is expressed as a factor of its dimension's base unit.
UNITS: dict[str, dict[str, float]] = {
    "length": {"m": 1, "km": 1000, "cm": 0.01, "mm": 0.001, "mi": 1609.344, "ft": 0.3048, "in": 0.0254},
    "mass": {"kg": 1, "g": 0.001, "lb": 0.45359237, "oz": 0.028349523125},
    "volume": {"l": 1, "ml": 0.001, "gal": 3.785411784, "cup": 0.2365882365},
}
TEMPERATURE_UNITS = ("c", "f", "k")


def _to_celsius(v: float, unit: str) -> float:
    return {"c": v, "f": (v - 32) * 5 / 9, "k": v - 273.15}[unit]


def _from_celsius(v: float, unit: str) -> float:
    return {"c": v, "f": v * 9 / 5 + 32, "k": v + 273.15}[unit]


def convert_units(value: float, from_unit: str, to_unit: str) -> dict:
    """Convert between units of length, mass, volume or temperature."""
    f, t = from_unit.strip().lower(), to_unit.strip().lower()
    if f in TEMPERATURE_UNITS and t in TEMPERATURE_UNITS:
        result = _from_celsius(_to_celsius(value, f), t)
        return {"value": round(result, 6), "unit": t, "dimension": "temperature"}
    for dimension, table in UNITS.items():
        if f in table and t in table:
            result = value * table[f] / table[t]
            return {"value": round(result, 6), "unit": t, "dimension": dimension}
    raise ValueError(f"can't convert {from_unit!r} to {to_unit!r}. Supported units:\n{units_reference()}")


def units_reference() -> str:
    """A human-readable list of supported units (exposed as an MCP *resource*)."""
    lines = [f"- {dim}: {', '.join(table)}" for dim, table in UNITS.items()]
    lines.append(f"- temperature: {', '.join(TEMPERATURE_UNITS)}")
    return "\n".join(lines)


ABOUT = """\
mcp-lab toolbox server.

Tools: calculate (safe arithmetic), roll_dice, convert_units.
Resources: lab://about (this text), lab://units (supported units).
Prompts: plan_recipe_conversion (turns a recipe into metric step by step).
"""


def recipe_prompt(recipe: str, target_system: str = "metric") -> str:
    """Text for the plan_recipe_conversion *prompt*."""
    return (
        f"Convert every quantity in the recipe below to {target_system} units. "
        "Use the convert_units tool for each conversion rather than doing it in your head, "
        "then rewrite the recipe with the converted amounts.\n\n"
        f"Recipe:\n{recipe}"
    )
