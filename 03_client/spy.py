#!/usr/bin/env python3
"""A stdio "wiretap": sits between any MCP client and any stdio MCP server and
logs every JSON-RPC message that passes through.

    python 03_client/spy.py -- <server command...>

Because MCP over stdio is just lines of JSON, a proxy is about 40 lines of code.
Point Claude Desktop, Claude Code or your agent at this script instead of the
real server and you'll see exactly what your host sends:

    claude mcp add lab-spy -- python /path/to/mcp-lab/03_client/spy.py -- uv run --directory /path/to/mcp-lab 02_sdk/server.py

Messages go to stderr (hosts usually show it in their MCP logs) and to
wire.log in the repo root.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parents[1] / "wire.log"
_lock = threading.Lock()


def record(direction: str, line: str, log) -> None:
    try:
        msg = json.loads(line)
        label = msg.get("method") or ("error" if "error" in msg else "result")
        pretty = json.dumps(msg, indent=2)
    except json.JSONDecodeError:
        label, pretty = "non-JSON!", line
    stamp = time.strftime("%H:%M:%S")
    entry = f"[{stamp}] {direction} {label} id={msg.get('id') if isinstance(msg, dict) else '-'}\n{pretty}\n"
    with _lock:
        sys.stderr.write(entry)
        sys.stderr.flush()
        log.write(entry)
        log.flush()


def pump(src, dst, direction: str, log) -> None:
    for line in iter(src.readline, ""):
        if line.strip():
            record(direction, line.strip(), log)
        dst.write(line)
        dst.flush()
    dst.close()


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        sys.exit("usage: spy.py -- <server command...>")

    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    with LOG_PATH.open("a") as log:
        log.write(f"\n===== spy started: {' '.join(argv)} =====\n")
        up = threading.Thread(target=pump, args=(sys.stdin, child.stdin, "client ──▶ server", log), daemon=True)
        up.start()
        pump(child.stdout, sys.stdout, "client ◀── server", log)
    sys.exit(child.wait())


if __name__ == "__main__":
    main()
