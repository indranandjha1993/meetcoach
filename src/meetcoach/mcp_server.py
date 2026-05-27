"""MCP server that lets Claude Code subscribe to the live meetcoach transcript.

Exposes three tools:
  - get_state: is meetcoach running, how many lines so far?
  - read_transcript: full or tail read
  - wait_for_new_lines: blocks server-side until new lines arrive (or timeout),
    then returns them. One blocking call per loop iteration in the agent =
    no visible `sleep 15` spam in the Claude Code chat.

Run via:
  python -m meetcoach.mcp_server      (stdio transport, for Claude Code)
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

TRANSCRIPT_PATH = Path(
    os.environ.get("MEETCOACH_TRANSCRIPT", str(Path.home() / ".meetcoach" / "current.txt"))
)
POLL_INTERVAL_S = 0.5  # server-side poll cadence; invisible to the agent

mcp = FastMCP("meetcoach")


def _read_lines() -> list[str]:
    if not TRANSCRIPT_PATH.exists():
        return []
    try:
        return TRANSCRIPT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


@mcp.tool()
async def get_state() -> dict:
    """Return whether meetcoach is running and the current transcript stats.

    Output:
      available: bool — true if the transcript file exists
      transcript_path: str
      total_lines: int
      latest_line: str | None
    """
    if not TRANSCRIPT_PATH.exists():
        return {
            "available": False,
            "transcript_path": str(TRANSCRIPT_PATH),
            "total_lines": 0,
            "latest_line": None,
        }
    lines = _read_lines()
    return {
        "available": True,
        "transcript_path": str(TRANSCRIPT_PATH),
        "total_lines": len(lines),
        "latest_line": lines[-1] if lines else None,
    }


@mcp.tool()
async def read_transcript(from_index: int = 0, max_lines: int = 0) -> dict:
    """Read transcript lines starting at `from_index` (0-based).

    Args:
      from_index: 0-based line offset to start at. 0 returns the whole transcript.
      max_lines: cap on returned lines (0 = no cap). Useful for context limits.

    Output:
      available: bool
      lines: list[str]
      from_index: int (echoed back)
      total_lines: int (current total in file)
    """
    if not TRANSCRIPT_PATH.exists():
        return {"available": False, "lines": [], "from_index": from_index, "total_lines": 0}
    lines = _read_lines()
    total = len(lines)
    start = max(0, from_index)
    selected = lines[start:]
    if max_lines > 0 and len(selected) > max_lines:
        selected = selected[-max_lines:]
        start = total - len(selected)
    return {
        "available": True,
        "lines": selected,
        "from_index": start,
        "total_lines": total,
    }


@mcp.tool()
async def wait_for_new_lines(since_index: int, timeout_s: int = 60) -> dict:
    """Block server-side until the transcript has more than `since_index` lines.

    Returns immediately if new lines already exist. Returns with an empty
    `lines` list (and unchanged `new_index`) when the timeout elapses; the
    agent should call again in that case.

    Args:
      since_index: highest line index the agent has already seen.
      timeout_s: max seconds to block (clamped to [1, 300]).

    Output:
      lines: list[str] — new lines (may be empty on timeout)
      new_index: int — total line count to pass as `since_index` next call
      available: bool — false if the transcript file disappeared mid-wait
    """
    timeout_s = max(1, min(300, int(timeout_s)))
    deadline = asyncio.get_running_loop().time() + timeout_s

    while asyncio.get_running_loop().time() < deadline:
        if not TRANSCRIPT_PATH.exists():
            await asyncio.sleep(POLL_INTERVAL_S)
            continue
        lines = _read_lines()
        if len(lines) > since_index:
            return {
                "available": True,
                "lines": lines[since_index:],
                "new_index": len(lines),
            }
        await asyncio.sleep(POLL_INTERVAL_S)

    lines = _read_lines()
    return {
        "available": TRANSCRIPT_PATH.exists(),
        "lines": [],
        "new_index": len(lines) if lines else since_index,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
