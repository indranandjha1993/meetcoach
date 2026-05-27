"""Single source of truth for "is meetcoach ready?" checks.

The doctor command, the start-time gate, the TUI status footer, the
startup readiness modal, and the toggleable detail view all read from
`check_all()` so behaviour stays consistent everywhere.

Each capability declares:
- name + group (audio / transcription / coach / mcp / slash-commands)
- a runtime state (OK / DEGRADED / BROKEN / DISABLED)
- a one-line `detail` explaining the current state
- multi-line `fix_steps` instructions for any non-OK state
- optional `palette_command` — the runtime fix the TUI palette wires up
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from meetcoach.audio import find_blackhole, find_default_mic
from meetcoach.providers import PROVIDER_CLASSES

if TYPE_CHECKING:
    from meetcoach.config import Settings


class CapabilityState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    BROKEN = "broken"
    DISABLED = "disabled"  # turned off by config (e.g., --no-mic)


@dataclass(slots=True)
class Capability:
    name: str
    group: str  # audio / transcription / coach / mcp / slash-commands
    state: CapabilityState
    detail: str = ""
    fix_steps: list[str] = field(default_factory=list)
    palette_command: str | None = None

    @property
    def is_broken(self) -> bool:
        return self.state == CapabilityState.BROKEN


# ---------- audio ----------


def _check_blackhole() -> Capability:
    driver = Path("/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver")
    device_idx = find_blackhole()
    if device_idx is not None:
        return Capability(
            name="BlackHole 2ch",
            group="audio",
            state=CapabilityState.OK,
            detail=f"device index {device_idx}",
        )
    if driver.exists():
        return Capability(
            name="BlackHole 2ch",
            group="audio",
            state=CapabilityState.BROKEN,
            detail="driver installed but Core Audio hasn't picked it up",
            fix_steps=[
                "sudo killall coreaudiod    # restart Core Audio daemon",
                "# or just reboot",
            ],
        )
    return Capability(
        name="BlackHole 2ch",
        group="audio",
        state=CapabilityState.BROKEN,
        detail="driver not installed (system audio capture will not work)",
        fix_steps=[
            "make audio-setup",
            "# or: brew install --cask blackhole-2ch && sudo killall coreaudiod",
        ],
    )


def _check_mic() -> Capability:
    idx = find_default_mic()
    if idx is None:
        return Capability(
            name="Default microphone",
            group="audio",
            state=CapabilityState.BROKEN,
            detail="no input device detected",
            fix_steps=[
                "Plug in or enable a microphone in System Settings > Sound > Input.",
            ],
        )
    return Capability(
        name="Default microphone",
        group="audio",
        state=CapabilityState.OK,
        detail=f"device index {idx}",
    )


def _check_multi_output() -> Capability:
    """Detect whether the current default output is a Multi-Output Device.

    Best-effort: parses `system_profiler SPAudioDataType` text output. Not a
    deep check — only catches the "default output is a plain device, not a
    multi-output" case.
    """
    try:
        out = subprocess.run(
            ["system_profiler", "SPAudioDataType"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return Capability(
            name="Multi-Output Device",
            group="audio",
            state=CapabilityState.DEGRADED,
            detail="could not query Core Audio (system_profiler missing or slow)",
        )
    # Parse: per-device block has indented lines; the device name ends with ":".
    # `system_profiler` doesn't expose a "this is an aggregate device" tag,
    # but aggregate/virtual devices report `Transport: Unknown` while real
    # hardware reports `Transport: Built-in/USB/Bluetooth/etc.`
    lines = out.splitlines()
    default_device: str | None = None
    current: str | None = None
    transport: dict[str, str] = {}
    for ln in lines:
        stripped = ln.strip()
        if ln.startswith("        ") and stripped.endswith(":") and ":" not in stripped[:-1]:
            current = stripped[:-1]
        elif stripped == "Default Output Device: Yes" and current:
            default_device = current
        elif stripped.startswith("Transport: ") and current:
            transport[current] = stripped[len("Transport: ") :].strip()
    if not default_device:
        return Capability(
            name="Multi-Output / system-audio route",
            group="audio",
            state=CapabilityState.DEGRADED,
            detail="no default output device detected",
        )
    name_lower = default_device.lower()
    routes_to_blackhole = "blackhole" in name_lower
    looks_aggregate = transport.get(default_device, "").lower() == "unknown"
    if routes_to_blackhole:
        return Capability(
            name="System-audio route",
            group="audio",
            state=CapabilityState.OK,
            detail=f'default output is "{default_device}" (system audio captured directly)',
        )
    if looks_aggregate:
        return Capability(
            name="System-audio route",
            group="audio",
            state=CapabilityState.OK,
            detail=f'default output is "{default_device}" (aggregate / multi-output)',
        )
    return Capability(
        name="System-audio route",
        group="audio",
        state=CapabilityState.BROKEN,
        detail=(
            f'default output is "{default_device}" — looks like a plain device, '
            f"so system audio probably isn't reaching BlackHole"
        ),
        fix_steps=[
            "Create a Multi-Output Device in Audio MIDI Setup (Spotlight: 'audio midi'):",
            "  Click + → Create Multi-Output Device",
            "  Check BlackHole 2ch + your usual output (headphones/speakers)",
            "  Set Primary Device to your usual output (not BlackHole)",
            "  Tick Drift Correction on BlackHole only",
            "Then: System Settings > Sound > Output → select the new device",
        ],
    )


# ---------- transcription ----------


def _check_deepgram(settings: Settings) -> Capability:
    if not settings.deepgram_key:
        return Capability(
            name="Deepgram API key",
            group="transcription",
            state=CapabilityState.BROKEN,
            detail="not configured — live transcription will not work",
            fix_steps=[
                "Get a free key at https://deepgram.com  ($200 credit, ~750 hours)",
                "cp .env.example .env",
                "# edit .env and paste:  DEEPGRAM_API_KEY=your_key_here",
                "Or use local Whisper instead: meetcoach start --engine whisper",
            ],
            palette_command="set_deepgram_key",
        )
    return Capability(
        name="Deepgram API key",
        group="transcription",
        state=CapabilityState.OK,
        detail=f"configured ({len(settings.deepgram_key)} chars)",
    )


def _check_whisper(settings: Settings) -> Capability:
    engine = settings.resolve_engine()
    if engine != "whisper":
        return Capability(
            name="Whisper (fallback)",
            group="transcription",
            state=CapabilityState.DISABLED,
            detail=f"not loaded (engine={engine})",
        )
    missing: list[str] = []
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        missing.append("faster-whisper")
    try:
        import webrtcvad  # noqa: F401
    except ImportError:
        missing.append("webrtcvad")
    if missing:
        return Capability(
            name="Whisper backend",
            group="transcription",
            state=CapabilityState.BROKEN,
            detail=f"missing: {', '.join(missing)}",
            fix_steps=[
                "uv pip install 'meetcoach[whisper]'",
                "Or use Deepgram instead by setting DEEPGRAM_API_KEY in .env.",
            ],
        )
    return Capability(
        name="Whisper backend",
        group="transcription",
        state=CapabilityState.OK,
        detail="faster-whisper + webrtcvad installed",
    )


# ---------- coach ----------


def _check_coach_provider(settings: Settings) -> Capability:
    name = settings.coach_provider
    cls = PROVIDER_CLASSES.get(name)
    if cls is None:
        return Capability(
            name=f"Coach provider ({name})",
            group="coach",
            state=CapabilityState.BROKEN,
            detail="unknown provider name",
            fix_steps=[
                f"Set --coach-provider to one of: {', '.join(PROVIDER_CLASSES)}",
                "Or set COACH_PROVIDER in .env",
            ],
        )
    path = cls.resolved_path(settings.coach_bin)
    if path is None:
        return Capability(
            name=f"Coach provider ({name})",
            group="coach",
            state=CapabilityState.BROKEN,
            detail="CLI not found on PATH",
            fix_steps=[
                f"Install: {cls.install_hint}",
                "Or switch provider: meetcoach start --coach-provider <claude|gemini|codex>",
            ],
            palette_command="switch_coach_provider",
        )
    return Capability(
        name=f"Coach provider ({name})",
        group="coach",
        state=CapabilityState.OK,
        detail=path,
    )


def _check_available_providers() -> list[Capability]:
    out = []
    for name, cls in PROVIDER_CLASSES.items():
        path = cls.resolved_path()
        if path:
            out.append(
                Capability(
                    name=f"  {name} CLI",
                    group="coach",
                    state=CapabilityState.OK,
                    detail=path,
                )
            )
        else:
            out.append(
                Capability(
                    name=f"  {name} CLI",
                    group="coach",
                    state=CapabilityState.DISABLED,
                    detail="not installed",
                    fix_steps=[f"Install: {cls.install_hint}"],
                )
            )
    return out


# ---------- mcp ----------


def _check_mcp_binary() -> Capability:
    path = shutil.which("meetcoach-mcp")
    if path:
        return Capability(
            name="meetcoach-mcp binary",
            group="mcp",
            state=CapabilityState.OK,
            detail=path,
        )
    # Fall back to the venv-local path
    repo_root = Path(__file__).resolve().parent.parent.parent
    venv_bin = repo_root / ".venv" / "bin" / "meetcoach-mcp"
    if venv_bin.exists():
        return Capability(
            name="meetcoach-mcp binary",
            group="mcp",
            state=CapabilityState.OK,
            detail=str(venv_bin),
        )
    return Capability(
        name="meetcoach-mcp binary",
        group="mcp",
        state=CapabilityState.BROKEN,
        detail="not found on PATH or in .venv",
        fix_steps=[
            "make install     # or: uv pip install -e .",
        ],
    )


def _check_mcp_registered_claude() -> Capability:
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return Capability(
            name="Claude Code MCP registration",
            group="mcp",
            state=CapabilityState.DEGRADED,
            detail=f"{settings_path} doesn't exist (is Claude Code installed?)",
        )
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return Capability(
            name="Claude Code MCP registration",
            group="mcp",
            state=CapabilityState.DEGRADED,
            detail=f"could not read settings.json: {e}",
        )
    servers = (data.get("mcpServers") or {}) if isinstance(data, dict) else {}
    if "meetcoach" not in servers:
        return Capability(
            name="Claude Code MCP registration",
            group="mcp",
            state=CapabilityState.BROKEN,
            detail="not registered — /meeting slash command won't work in claude",
            fix_steps=[
                "make register-mcp     # prints the JSON snippet",
                "# paste the printed mcpServers.meetcoach block into",
                f"# {settings_path}",
                "# restart `claude` after editing",
            ],
        )
    return Capability(
        name="Claude Code MCP registration",
        group="mcp",
        state=CapabilityState.OK,
        detail=f"registered in {settings_path}",
    )


# ---------- slash commands ----------


def _check_slash_commands() -> list[Capability]:
    repo_root = Path(__file__).resolve().parent.parent.parent
    claude_src = repo_root / "share" / "slash-commands" / "meeting.md"
    skill_src = repo_root / "share" / "skills" / "meeting"
    targets = {
        "claude": (Path.home() / ".claude" / "commands" / "meeting.md", claude_src),
        "cursor": (Path.home() / ".cursor" / "skills-cursor" / "meeting", skill_src),
        "gemini": (Path.home() / ".gemini" / "skills" / "meeting", skill_src),
        "codex": (Path.home() / ".codex" / "skills" / "meeting", skill_src),
    }
    out = []
    for tool, (dst, expected_src) in targets.items():
        marker_dir = Path.home() / f".{tool}"
        if not marker_dir.exists():
            out.append(
                Capability(
                    name=f"  /meeting ({tool})",
                    group="slash-commands",
                    state=CapabilityState.DISABLED,
                    detail=f"{tool} not installed on this machine",
                )
            )
            continue
        if dst.is_symlink() and dst.resolve() == expected_src.resolve():
            out.append(
                Capability(
                    name=f"  /meeting ({tool})",
                    group="slash-commands",
                    state=CapabilityState.OK,
                    detail=str(dst),
                )
            )
        elif dst.exists():
            out.append(
                Capability(
                    name=f"  /meeting ({tool})",
                    group="slash-commands",
                    state=CapabilityState.DEGRADED,
                    detail=f"exists but not linked to repo ({dst})",
                    fix_steps=["make slash-commands"],
                )
            )
        else:
            out.append(
                Capability(
                    name=f"  /meeting ({tool})",
                    group="slash-commands",
                    state=CapabilityState.BROKEN,
                    detail=f"not installed at {dst}",
                    fix_steps=["make slash-commands"],
                )
            )
    return out


# ---------- aggregate ----------


def check_all(settings: Settings) -> list[Capability]:
    """All static checks (no runtime state required).

    For per-stream connection state (Deepgram WebSocket up/down etc.) the
    TUI layers its own dynamic info on top of these.
    """
    is_macos = os.uname().sysname == "Darwin"

    caps: list[Capability] = []

    # Audio
    if is_macos:
        caps.append(_check_blackhole())
        caps.append(_check_multi_output())
    caps.append(_check_mic())

    # Transcription
    engine = settings.resolve_engine()
    if engine == "deepgram":
        caps.append(_check_deepgram(settings))
    caps.append(_check_whisper(settings))

    # Coach
    caps.append(_check_coach_provider(settings))
    caps.extend(_check_available_providers())

    # MCP
    caps.append(_check_mcp_binary())
    caps.append(_check_mcp_registered_claude())

    # Slash commands
    caps.extend(_check_slash_commands())

    return caps


def broken_capabilities(caps: list[Capability]) -> list[Capability]:
    return [c for c in caps if c.is_broken]


def group_by(caps: list[Capability]) -> dict[str, list[Capability]]:
    groups: dict[str, list[Capability]] = {}
    for c in caps:
        groups.setdefault(c.group, []).append(c)
    return groups
