#!/usr/bin/env bash
# Stage 05: speak MCP over HTTP with nothing but curl.
# Start the server first:  uv run 05_http/server.py
set -euo pipefail
URL="${1:-http://127.0.0.1:8000/mcp}"
AUTH=()
[[ -n "${MCP_LAB_TOKEN:-}" ]] && AUTH=(-H "Authorization: Bearer $MCP_LAB_TOKEN")

META='"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1"},"io.modelcontextprotocol/clientCapabilities":{}}'

post() {  # post <method> <Mcp-Name or ""> <params-json-without-braces>
  local method=$1 name=$2 params=$3
  local headers=(-H "Content-Type: application/json"
                 -H "Accept: application/json, text/event-stream"
                 -H "MCP-Protocol-Version: 2026-07-28"
                 -H "Mcp-Method: $method")
  [[ -n "$name" ]] && headers+=(-H "Mcp-Name: $name")
  local body="{\"jsonrpc\":\"2.0\",\"id\":$RANDOM,\"method\":\"$method\",\"params\":{${params:+$params,}$META}}"
  echo -e "\n\033[34m▶ POST $method ${name:+(Mcp-Name: $name)}\033[0m"
  curl -sS -w '\nHTTP %{http_code}\n' "${headers[@]}" "${AUTH[@]}" -d "$body" "$URL"
}

post server/discover "" ""
post tools/list "" ""
post tools/call convert_units '"name":"convert_units","arguments":{"value":10,"from_unit":"lb","to_unit":"kg"}'
post resources/read lab://units/mass '"uri":"lab://units/mass"'

echo -e "\n\033[33m▶ Mcp-Name header says one tool, body says another (should be 400 HeaderMismatch):\033[0m"
curl -sS -w '\nHTTP %{http_code}\n' "${AUTH[@]}" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "MCP-Protocol-Version: 2026-07-28" \
  -H "Mcp-Method: tools/call" -H "Mcp-Name: roll_dice" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"calculate\",\"arguments\":{\"expression\":\"1+1\"},$META}}" "$URL"
