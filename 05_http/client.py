"""Stage 05 — SDK client over HTTP. Start 05_http/server.py first.

    uv run 05_http/client.py
    uv run 05_http/client.py http://my-homeserver:8000/mcp

The only change from the stdio client is what we pass to Client(): a URL
instead of a command. (Set MCP_LAB_TOKEN if the server requires it.)
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx2  # the HTTP library the SDK itself uses
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import TextContent


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"
    token = os.getenv("MCP_LAB_TOKEN")
    if token:
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
        target = streamable_http_client(url, http_client=http)
    else:
        target = url  # Client() accepts a plain URL string too

    async with Client(target) as client:
        print("server:", client.server_info)
        print("tools:", [t.name for t in (await client.list_tools()).tools])
        result = await client.call_tool("convert_units", {"value": 5, "from_unit": "km", "to_unit": "mi"})
        print("5 km in miles:", "".join(b.text for b in result.content if isinstance(b, TextContent)))


if __name__ == "__main__":
    asyncio.run(main())
