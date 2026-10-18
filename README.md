# mcp-lab

Learn the **Model Context Protocol** by building it up one layer at a time.
First a server written by hand with no SDK, then the same server with the
official SDK, then a client, then your own LLM agent that uses MCP servers as
its tools, and finally the same server over HTTP.

Everything targets the current spec, **MCP 2026-07-28**, and the Python SDK
**`mcp` 2.x**. If you've read older tutorials, the big change is that the
protocol is now **stateless**: there is no `initialize` handshake and no
session. Every request carries its protocol version and the client's
capabilities in `params._meta` (see [Concepts](#concepts) below).

```
mcp-lab/
├── src/mcp_lab/toolbox.py   plain Python "business logic" (calculator, dice, unit converter)
├── 01_raw/                  server.py: MCP by hand, stdlib only │ poke.py: you play the client
├── 02_sdk/                  server.py: same server with MCPServer (about 1/3 the code)
├── 03_client/               client.py: SDK client, no LLM │ spy.py: wiretap proxy for any stdio server
├── 04_agent/                agent.py: OpenRouter-powered agent that uses MCP servers │ servers.json
├── 05_http/                 server.py: Streamable HTTP + bearer auth │ client.py │ curl.sh
└── tests/                   uv run pytest -q   (no API key needed)
```

## Setup

```bash
cd mcp-lab
uv sync                        # creates .venv with mcp, openai, uvicorn...
cp .env.example .env           # then paste your OpenRouter key (only stage 04 needs it)
uv run pytest -q               # 5 tests, all stages
```

---

## Stage 01 · The protocol by hand (`01_raw/`)

**Goal:** see that MCP is only JSON-RPC messages over a pipe.

```bash
uv run 01_raw/poke.py          # scripted conversation with every message pretty-printed
```

Or type the messages yourself. Paste these lines into `uv run 01_raw/server.py`:

```json
{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"roll_dice","arguments":{"sides":20},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}
```

What to notice in `server.py`:

* **stdio transport.** One JSON message per line on stdin/stdout, and logs go
  to **stderr**. A stray `print()` corrupts the stream, and a missing
  `flush()` makes clients hang. Those are the two classic bugs.
* **Requests vs notifications.** A message with an `id` gets exactly one
  reply with that `id`. A message without one gets no reply.
* **Two kinds of failure.** An unknown tool is a *protocol error* (a JSON-RPC
  `error`). A tool that runs and fails, like `1/0`, returns a *result* with
  `isError: true`, so the LLM reads the message and can retry.
* **Stateless validation.** Every request is checked for `_meta`. An
  unsupported version gets error `-32022` along with the versions the server
  does support.
* **Caching hints.** `server/discover`, `*/list` and `resources/read`
  results must carry `ttlMs` and `cacheScope`. I learned this the hard way:
  the SDK client rejects results without them. Delete them in `cacheable()`
  and run stage 03 to see the error.

**Exercises:** add a `flip_coin` tool, then run `poke.py` and check it shows
up in `tools/list`. Break the flush and watch `03_client/client.py` hang.

## Stage 02 · The same server with the SDK (`02_sdk/`)

```bash
uv run 01_raw/poke.py uv run 02_sdk/server.py   # same hand-written messages, SDK server
```

Put `01_raw/server.py` next to `02_sdk/server.py`. With the SDK:

* the JSON Schemas come from type hints, `Annotated[..., Field(...)]` and
  docstrings,
* the return type annotation (a `TypedDict`) produces both `content` and
  `structuredContent`,
* `@mcp.resource("lab://units/{dimension}")` is a **resource template** (a
  parameterised URI), which would be fiddly to write by hand,
* exceptions are hidden from the model by default. Raise `ToolError` when you
  want the model to see the message.

One difference you'll see in `poke.py` output: the SDK reports an unknown
tool as `isError: true` rather than as a JSON-RPC error.

## Stage 03 · A client (`03_client/`)

```bash
uv run 03_client/client.py                     # official SDK client ↔ YOUR hand-written server
uv run 03_client/client.py 02_sdk/server.py    # ↔ SDK server
uv run 03_client/client.py --spy               # also log the traffic to stderr and wire.log
```

The client is the half that hosts like Claude Desktop, Claude Code and your
own agent implement. `async with Client(...)` launches the server, and the
first thing it sends is a `server/discover` probe (look in `wire.log`).

`spy.py` is a ~40-line stdio proxy. Because stdio MCP is just lines of JSON,
you can drop it in front of **any** server, including ones Claude Code uses,
and watch what a real host sends (see
[Use it from Claude Code](#use-it-from-claude-code--claude-desktop)).

## Stage 04 · Your own agent (`04_agent/`)

```bash
uv run 04_agent/agent.py "what's 26.2 miles in km? also roll 3d6"
uv run 04_agent/agent.py --confirm             # REPL, approve each tool call
```

The agent is an MCP **host**. It starts one client per server in
`servers.json`, converts each MCP tool into an OpenAI-style function
definition, and runs the standard loop: the model asks for tool calls, the
agent runs them through `tools/call`, feeds the results back, and repeats
until the model answers in text.

Each primitive reaches the model a different way, and this is the part of MCP
that's easiest to misunderstand:

| Primitive | Who's in control | How this agent exposes it |
|---|---|---|
| Tools | the **model** | sent as functions on every LLM call, namespaced `server__tool` |
| Resources | the **application** | listed in the system prompt and readable through a built-in `read_resource` tool; `/read toolbox lab://units` attaches one yourself |
| Prompts | the **user** | slash commands: `/prompt toolbox plan_recipe_conversion recipe="2 cups flour, 1 lb butter"` |

REPL commands: `/tools`, `/resources`, `/read`, `/prompts`, `/prompt`, `/quit`.

**Models.** The agent works with any OpenAI-compatible endpoint. It defaults
to OpenRouter with `google/gemini-3.8-flash`, which costs a fraction of a cent
per question. Set `LLM_MODEL` in `.env` to switch. To list models that
support tool calling, including free ones:

```bash
curl -s "https://openrouter.ai/api/v1/models?supported_parameters=tools" | python3 -c \
  "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if m['id'].endswith(':free')]"
```

Small free models call tools less reliably. When the agent misbehaves, try a
stronger model before you debug your code. For Ollama, set
`LLM_BASE_URL=http://localhost:11434/v1`, any non-empty `OPENROUTER_API_KEY`,
and `LLM_MODEL=qwen3:8b`.

**Using community servers.** `servers.json` already has entries (disabled)
for two reference servers from
[modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers):
`filesystem` (needs Node) and `fetch` (runs through `uvx`). Enable one and
your agent can read files or fetch web pages without you writing any of that
code. That's the point of MCP: you write the agent once, and you add
capabilities by adding servers. Before you enable one, check that server's
README for the protocol version it supports. The SDK client also speaks the
older handshake versions.

**Security notes worth knowing.**
* Servers get a minimal environment, not your shell's, so your API key doesn't
  leak to third-party servers. Pass a secret explicitly with `"env"` in
  `servers.json`.
* Tool descriptions and results come from the server and go straight into the
  model's context, so treat them as untrusted input. `--confirm` is your
  human in the loop.

## Stage 05 · Over HTTP (`05_http/`)

```bash
uv run 05_http/server.py                       # terminal 1
bash 05_http/curl.sh                           # terminal 2: raw HTTP
uv run 05_http/client.py                       # or the SDK client
```

None of the tool code changes. Only the transport does. Each JSON-RPC request
becomes one HTTP POST, and the protocol fields are mirrored into headers
(`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`) so load balancers and
gateways can route without parsing the body. The last request in `curl.sh`
deliberately sends mismatched headers and gets `400 HeaderMismatch`. There
are no sessions anymore, so the server scales like any stateless web
service.

* Auth: `MCP_LAB_TOKEN=s3cret uv run 05_http/server.py` turns on a toy bearer
  check, and the client and curl script send the token from the same variable.
  Production servers use the OAuth 2.1 flow from the MCP authorization spec.
* Remote: on your home server, run with `MCP_LAB_HOST=0.0.0.0` and a token,
  then point the agent at it with `{"url": "http://<host>:8000/mcp"}` in
  `servers.json`. Add the token as a header in `make_client` if you've turned
  auth on.

---

## Use it from Claude Code / Claude Desktop

Your servers work in real hosts unchanged:

```bash
# Claude Code
claude mcp add lab -- uv run --directory /path/to/mcp-lab 02_sdk/server.py
# ...or with the wiretap, to see what Claude Code sends:
claude mcp add lab-spy -- uv run --directory /path/to/mcp-lab python 03_client/spy.py -- python 02_sdk/server.py
```

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`).
Use absolute paths, because Desktop doesn't launch from your shell:

```json
{
  "mcpServers": {
    "mcp-lab": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--directory", "/Users/YOU/path/to/mcp-lab", "02_sdk/server.py"]
    }
  }
}
```

A host built on an older protocol version may only speak the `initialize`
handshake. The hand-written server from stage 01 is modern-only, so such a
host gets a clear error. The SDK server from stage 02 speaks both eras.

## Concepts

```
 ┌──────────── Host (Claude Desktop, Claude Code, 04_agent) ────────────┐
 │  LLM  ◀──▶  host logic  ──▶ MCP client ─┬─ stdio ──▶ MCP server A     │
 │                                         └─ HTTP  ──▶ MCP server B     │
 └──────────────────────────────────────────────────────────────────────┘
```

* **Host**: the app the user talks to. It owns the LLM and decides what
  context the model sees.
* **Client**: one connection to one server, living inside the host.
* **Server**: exposes **tools** (actions), **resources** (data, addressed by
  URI) and **prompts** (templates).
* **Transport**: stdio (the host launches the server as a subprocess) or
  Streamable HTTP (the server runs independently).
* **2026-07-28 changes**: per-request `_meta` replaces `initialize`;
  `server/discover` is optional for clients; list results carry cache hints;
  server→client requests (sampling, elicitation) are no longer separate
  messages and come back inside an `input_required` result that the client
  answers by retrying (MRTR); change notifications arrive through
  `subscriptions/listen`.

**Where to go next:** add a tool that uses elicitation (the server asks the
user something mid-call) or sampling (the server borrows the client's LLM).
Both now use the multi-round-trip (MRTR) pattern. Or wrap something real (your
notes vault, your homelab's Docker) as a server and plug it into the agent.

References: [MCP spec 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) ·
[Build a client](https://modelcontextprotocol.io/docs/2026-07-28/develop/build-client) ·
[Reference servers](https://github.com/modelcontextprotocol/servers) ·
[Anthropic's announcement](https://www.anthropic.com/news/model-context-protocol)
