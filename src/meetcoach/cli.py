from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from meetcoach.audio import find_blackhole, find_default_mic, find_device, list_devices
from meetcoach.capabilities import CapabilityState, broken_capabilities, check_all, group_by
from meetcoach.config import Settings
from meetcoach.providers import PROVIDER_CLASSES

console = Console()

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT_BODY_PATH = REPO_ROOT / "share" / "skills" / "meeting" / "SKILL.md"


@click.group()
def cli() -> None:
    """Live meeting transcription + Claude coach."""


@cli.command()
@click.option("--raw", is_flag=True, help="Include frontmatter (skill metadata) in the output.")
def prompt(raw: bool) -> None:
    """Print the /meeting prompt to stdout (paste into any LLM tool's commands).

    Useful for platforms we don't ship an installer for, or for piping into
    your tool's "create command" flow:

        meetcoach prompt | pbcopy
    """
    if not PROMPT_BODY_PATH.exists():
        console.print(f"[red]prompt source missing:[/] {PROMPT_BODY_PATH}")
        sys.exit(2)
    text = PROMPT_BODY_PATH.read_text(encoding="utf-8")
    if raw:
        click.echo(text)
        return
    # Strip the YAML frontmatter if present (between leading "---" pair)
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5 :].lstrip()
    click.echo(text)


@cli.command()
def devices() -> None:
    """List input audio devices."""
    table = Table(title="Input devices")
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("ch", justify="right")
    table.add_column("rate", justify="right")
    for d in list_devices():
        table.add_row(
            str(d["index"]),
            d["name"],
            str(d["max_input_channels"]),
            f"{int(d['default_samplerate'])}",
        )
    console.print(table)


_STATE_GLYPH = {
    CapabilityState.OK: "[green]✓[/]",
    CapabilityState.DEGRADED: "[yellow]![/]",
    CapabilityState.BROKEN: "[red]✗[/]",
    CapabilityState.DISABLED: "[dim]-[/]",
}

_GROUP_TITLES = {
    "audio": "Audio",
    "transcription": "Transcription",
    "coach": "Coach LLM provider",
    "mcp": "MCP server",
    "slash-commands": "/meeting handler",
}


def _render_capabilities(verbose: bool = False) -> bool:
    """Print check_all() results as a grouped table. Returns False if anything broken."""
    settings = Settings()
    caps = check_all(settings)
    grouped = group_by(caps)
    for group, title in _GROUP_TITLES.items():
        items = grouped.get(group, [])
        if not items:
            continue
        console.print(f"[bold]{title}[/]")
        for c in items:
            glyph = _STATE_GLYPH[c.state]
            console.print(f"  {glyph} {c.name:36s}  [dim]{c.detail}[/]")
            if verbose and c.fix_steps and c.state != CapabilityState.OK:
                for step in c.fix_steps:
                    console.print(f"        [cyan]{step}[/]")
        console.print()

    broken = broken_capabilities(caps)
    if broken and not verbose:
        console.print(
            f"[yellow]{len(broken)} item(s) need attention.[/] "
            f"Run `meetcoach doctor --verbose` for fix instructions."
        )
    return not broken


@cli.command()
@click.option("-v", "--verbose", is_flag=True, help="Show fix instructions for any broken items.")
def doctor(verbose: bool) -> None:
    """Sanity-check the environment — audio, STT, coach providers, MCP, slash commands."""
    ok = _render_capabilities(verbose=verbose)
    sys.exit(0 if ok else 1)


@cli.command()
@click.option("--mic", "mic", default=None, help="Mic device (index or substring of name).")
@click.option(
    "--system",
    "system",
    default=None,
    help="System-audio device (index or substring; usually 'BlackHole').",
)
@click.option(
    "--engine",
    type=click.Choice(["auto", "deepgram", "whisper"]),
    default="auto",
    show_default=True,
)
@click.option("--whisper-model", default="small.en", show_default=True)
@click.option(
    "--interval",
    type=float,
    default=25.0,
    show_default=True,
    help="Seconds between coach ticks.",
)
@click.option(
    "--coach-provider",
    type=click.Choice(list(PROVIDER_CLASSES.keys())),
    default=None,
    help="LLM provider for the coach (default: claude, override via COACH_PROVIDER env).",
)
@click.option(
    "--coach-bin",
    default=None,
    help="Override the binary path for the chosen provider.",
)
@click.option(
    "--model", "coach_model", default=None,
    help="Model name passed through to the coach CLI (e.g. claude-haiku-4-5).",
)
@click.option("--no-mic", is_flag=True, help="Skip mic capture (system audio only).")
@click.option("--no-system", is_flag=True, help="Skip system audio capture (mic only).")
@click.option(
    "--mic-label",
    default="You",
    show_default=True,
    help="Label for the mic speaker (your own voice).",
)
@click.option(
    "--names",
    default=None,
    help=(
        "Comma-separated names for remote speakers, in first-seen order. "
        "e.g. --names 'Vinay,Priya,Sam' maps speaker-0→Vinay, speaker-1→Priya, …"
    ),
)
def start(
    mic: str | None,
    system: str | None,
    engine: str,
    whisper_model: str,
    interval: float,
    coach_provider: str | None,
    coach_bin: str | None,
    coach_model: str | None,
    no_mic: bool,
    no_system: bool,
    mic_label: str,
    names: str | None,
) -> None:
    """Launch the live meeting TUI."""
    from meetcoach.tui import MeetCoachApp

    mic_idx = None if no_mic else (find_device(mic) if mic else find_default_mic())
    sys_idx = None if no_system else (find_device(system) if system else find_blackhole())

    # Only one hard-exit condition: zero audio devices. Without it the TUI
    # has literally nothing to capture. Everything else (missing Deepgram
    # key, missing coach CLI, missing Whisper deps, missing MCP registration)
    # becomes a soft warning here and a red indicator in the TUI status
    # panel + readiness modal — so the user can fix it without restarting.
    if mic_idx is None and sys_idx is None:
        console.print(
            "[red]✗ No audio sources resolved.[/] "
            "Run `meetcoach doctor` for diagnostic details."
        )
        sys.exit(2)
    if sys_idx is None and not no_system:
        console.print(
            "[yellow]! No BlackHole device found — capturing mic only.[/] "
            "Pass --no-system to silence, or install BlackHole via `make audio-setup`."
        )

    settings_defaults = Settings()
    chosen_provider = coach_provider or settings_defaults.coach_provider
    if chosen_provider not in PROVIDER_CLASSES:
        console.print(
            f"[red]✗ Unknown coach provider: {chosen_provider!r}.[/] "
            f"Available: {', '.join(PROVIDER_CLASSES)}"
        )
        sys.exit(2)

    name_list = [n.strip() for n in names.split(",") if n.strip()] if names else []
    settings = Settings(
        mic_device=mic_idx,
        system_device=sys_idx,
        engine=engine,
        whisper_model=whisper_model,
        coach_interval=interval,
        coach_provider=chosen_provider,
        coach_bin=coach_bin,
        coach_model=coach_model,
        mic_label=mic_label,
        names=name_list,
    )

    # Pre-check capabilities and surface any problems before the TUI opens —
    # but DON'T exit. The TUI's startup-readiness modal will show the same
    # info with actionable fix steps; this is just for users who scrolled
    # past the modal too fast or have a non-interactive terminal.
    caps = check_all(settings)
    broken = broken_capabilities(caps)
    if broken:
        console.print(
            f"[yellow]! {len(broken)} capability/capabilities need attention "
            "(meetcoach will open anyway):[/]"
        )
        for c in broken:
            console.print(f"  [red]✗[/] {c.name} — {c.detail}")
        console.print(
            "  Run `meetcoach doctor --verbose` for fix instructions, "
            "or address them from the TUI status panel."
        )

    MeetCoachApp(settings).run()


if __name__ == "__main__":
    cli()
