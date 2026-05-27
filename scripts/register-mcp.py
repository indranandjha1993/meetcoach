#!/usr/bin/env python3
"""Register the meetcoach MCP server with every detected LLM tool.

Auto-skips tools that aren't installed; idempotent — re-running detects
an existing registration and doesn't double-add. Exit code 0 if all
detected tools are registered (or skipped because not installed), 1 if
any registration attempt failed.

Per-tool mechanism:
  Claude Code:  `claude mcp add -s user meetcoach <bin>`
  Gemini CLI:   `gemini mcp add -s user meetcoach <bin>`
  Codex CLI:    `codex mcp add meetcoach -- <bin>`
  Cursor:       merge `{ mcpServers.meetcoach }` into ~/.cursor/mcp.json
                (no CLI — Cursor reads the JSON directly)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_BIN = REPO_ROOT / ".venv" / "bin" / "meetcoach-mcp"


def _run(cmd: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout, p.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def register_cli_tool(
    name: str,
    list_cmd: list[str],
    add_cmd: list[str],
    config_paths_to_check: list[Path] | None = None,
) -> str:
    """Generic helper for tools with `<tool> mcp list/add` subcommands.

    `config_paths_to_check` is a fallback — some tools' `mcp list` doesn't
    show user-scope entries (gemini bug as of writing), so we also grep
    the config file directly.

    Returns one of: "ok-existing", "ok-added", "fail".
    """
    rc, out, _ = _run(list_cmd)
    if rc == 0 and "meetcoach" in out:
        return "ok-existing"
    # Fallback: scan config files directly for "meetcoach"
    if config_paths_to_check:
        for cfg in config_paths_to_check:
            try:
                if cfg.exists() and "meetcoach" in cfg.read_text():
                    return "ok-existing"
            except OSError:
                continue
    rc, _, err = _run(add_cmd)
    if rc == 0:
        return "ok-added"
    print(f"  [error] {name} add failed: {err.strip()[:200]}", file=sys.stderr)
    return "fail"


def register_cursor() -> str:
    """Cursor uses ~/.cursor/mcp.json — no CLI. Merge-safely write to it."""
    cfg_path = Path.home() / ".cursor" / "mcp.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if cfg_path.exists() and cfg_path.stat().st_size > 0:
        try:
            existing = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            backup = cfg_path.with_suffix(
                f".json.bak.{datetime.now():%Y%m%d-%H%M%S}"
            )
            shutil.copy(cfg_path, backup)
            print(f"  [warn] backed up unparseable config to {backup}", file=sys.stderr)
            existing = {}

    servers = existing.setdefault("mcpServers", {})
    target = {"command": str(MCP_BIN), "args": []}
    if servers.get("meetcoach") == target:
        return "ok-existing"
    servers["meetcoach"] = target
    cfg_path.write_text(json.dumps(existing, indent=2) + "\n")
    return "ok-added"


def main() -> int:
    if not MCP_BIN.exists():
        print(f"✗ {MCP_BIN} missing — run 'make install' first", file=sys.stderr)
        return 1

    home = Path.home()
    plan = [
        ("claude", home / ".claude", shutil.which("claude") is not None,
         lambda: register_cli_tool(
             "claude",
             ["claude", "mcp", "list"],
             ["claude", "mcp", "add", "-s", "user", "meetcoach", str(MCP_BIN)],
             config_paths_to_check=[home / ".claude.json"],
         )),
        ("gemini", home / ".gemini", shutil.which("gemini") is not None,
         lambda: register_cli_tool(
             "gemini",
             ["gemini", "mcp", "list"],
             ["gemini", "mcp", "add", "-s", "user", "meetcoach", str(MCP_BIN)],
             config_paths_to_check=[home / ".gemini" / "settings.json"],
         )),
        ("codex", home / ".codex", shutil.which("codex") is not None,
         lambda: register_cli_tool(
             "codex",
             ["codex", "mcp", "list"],
             ["codex", "mcp", "add", "meetcoach", "--", str(MCP_BIN)],
             config_paths_to_check=[home / ".codex" / "config.toml"],
         )),
        ("cursor", home / ".cursor", True, register_cursor),  # cursor has no CLI
    ]

    ok_count = added_count = skip_count = fail_count = 0
    for name, marker, cli_present, register_fn in plan:
        if not marker.exists():
            print(f"  skip  {name:8s} ({name} not installed)")
            skip_count += 1
            continue
        if not cli_present and name != "cursor":
            print(f"  skip  {name:8s} ({name} config dir exists but binary missing)")
            skip_count += 1
            continue
        result = register_fn()
        if result == "ok-existing":
            print(f"  ok    {name:8s} (already registered)")
            ok_count += 1
        elif result == "ok-added":
            print(f"  add   {name:8s} (registered)")
            added_count += 1
        else:
            fail_count += 1

    print()
    print(
        f"Already registered: {ok_count}   "
        f"Newly added: {added_count}   "
        f"Skipped: {skip_count}   "
        f"Failed: {fail_count}"
    )
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
