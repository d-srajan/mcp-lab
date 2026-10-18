"""Stage 05 — the same SDK server, served over Streamable HTTP instead of stdio.

    uv run 05_http/server.py                      # http://127.0.0.1:8000/mcp
    MCP_LAB_TOKEN=s3cret uv run 05_http/server.py # require "Authorization: Bearer s3cret"

then, in another terminal:

    bash 05_http/curl.sh                          # raw HTTP, see the headers
    uv run 05_http/client.py                      # SDK client over HTTP
    # or enable the "http" server in 04_agent/servers.json and run the agent

stdio vs HTTP
-------------
stdio: the host *launches* the server as a child process; one server per host,
       local only, and the server inherits trust from your machine.
HTTP:  the server runs on its own (on your laptop, your home server, the cloud)
       and many clients POST to one endpoint. Each JSON-RPC request is its own
       HTTP POST, and the protocol fields are mirrored into headers
       (MCP-Protocol-Version, Mcp-Method, Mcp-Name) so proxies can route on them.
       Since 2026-07-28 there are no sessions (no Mcp-Session-Id), so you can
       put it behind a load balancer like any stateless web service.

Notice that none of the tool code changed. Transport and logic are separate.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Reuse the exact server object from stage 02. (Its directory isn't a Python package,
# so load it by path.)
mcp = runpy.run_path(str(ROOT / "02_sdk" / "server.py"), run_name="mcp_lab_sdk")["mcp"]


class BearerAuth(BaseHTTPMiddleware):
    """Toy auth so you can see where it plugs in. Real deployments should use the
    OAuth 2.1 flow from the MCP authorization spec (MCPServer(auth=..., token_verifier=...))."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request, call_next):
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def main() -> None:
    host = os.getenv("MCP_LAB_HOST", "127.0.0.1")  # bind to localhost unless you mean it
    port = int(os.getenv("MCP_LAB_PORT", "8000"))
    # json_response=True: answer each POST with plain JSON instead of an SSE stream.
    # Easier to read with curl; SSE matters once tools stream progress notifications.
    app = mcp.streamable_http_app(json_response=True)
    if token := os.getenv("MCP_LAB_TOKEN"):
        app.add_middleware(BearerAuth, token=token)
        print("bearer-token auth enabled", file=sys.stderr)
    print(f"MCP endpoint: http://{host}:{port}/mcp", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
