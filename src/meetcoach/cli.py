from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from meetcoach.audio import find_blackhole, find_default_mic, find_device, list_devices
from meetcoach.config import Settings
from meetcoach.providers import PROVIDER_CLASSES, detect_available_providers

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


@cli.command()
def doctor() -> None:
    """Sanity-check the environment."""
    settings = Settings()
    ok = True

    bh = find_blackhole()
    if bh is None:
        console.print("[red]✗[/] BlackHole not found. Install: brew install blackhole-2ch")
        console.print("  Then in Audio MIDI Setup, create a Multi-Output Device that includes")
        console.print("  both your speakers and BlackHole 2ch, and set Zoom/Meet/Teams output to it.")
        ok = False
    else:
        console.print(f"[green]✓[/] BlackHole found at device index {bh}")

    mic = find_default_mic()
    if mic is None:
        console.print("[red]✗[/] No default input mic detected.")
        ok = False
    else:
        console.print(f"[green]✓[/] Default mic at device index {mic}")

    console.print("[bold]Coach providers:[/]")
    any_provider_available = False
    for name, available, path in detect_available_providers():
        cls = PROVIDER_CLASSES[name]
        if available:
            mark = "[green]✓[/]"
            console.print(f"  {mark} {name:8s} {path}")
            any_provider_available = True
        else:
            mark = "[yellow]-[/]"
            console.print(f"  {mark} {name:8s} (install: {cls.install_hint})")
    if not any_provider_available:
        console.print("[red]✗[/] No coach providers available — install at least one")
        ok = False
    preferred = settings.coach_provider
    pref_cls = PROVIDER_CLASSES.get(preferred)
    if pref_cls is None:
        console.print(
            f"[red]✗[/] Configured coach_provider={preferred!r} is not a known provider. "
            f"Pick one of: {', '.join(PROVIDER_CLASSES.keys())}"
        )
        ok = False
    elif not pref_cls.is_available(settings.coach_bin):
        console.print(
            f"[red]✗[/] Configured coach_provider={preferred!r} but its CLI isn't installed."
        )
        ok = False
    else:
        console.print(f"[green]✓[/] Active coach provider: {preferred}")

    engine = settings.resolve_engine()
    if engine == "deepgram":
        console.print("[green]✓[/] DEEPGRAM_API_KEY set — using Deepgram streaming STT")
    else:
        console.print(
            "[yellow]![/] No DEEPGRAM_API_KEY — will use local faster-whisper "
            "(install with: pip install 'meetcoach[whisper]')"
        )
        try:
            import faster_whisper  # noqa: F401
            import webrtcvad  # noqa: F401

            console.print("[green]✓[/] faster-whisper + webrtcvad installed")
        except ImportError as e:
            console.print(f"[red]✗[/] whisper backend missing: {e}")
            ok = False

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

    if mic_idx is None and sys_idx is None:
        console.print("[red]No audio sources resolved.[/] Run `meetcoach doctor` for help.")
        sys.exit(2)
    if sys_idx is None and not no_system:
        console.print(
            "[yellow]No BlackHole device found — capturing mic only. "
            "Pass --no-system to silence this warning, or install BlackHole.[/]"
        )

    # Fail fast if the chosen provider's CLI is missing — better than silently
    # starting and only seeing errors in the coach pane every 25s.
    settings_defaults = Settings()
    chosen_provider = coach_provider or settings_defaults.coach_provider
    provider_cls = PROVIDER_CLASSES.get(chosen_provider)
    if provider_cls is None:
        console.print(
            f"[red]Unknown coach provider: {chosen_provider!r}.[/] "
            f"Available: {', '.join(PROVIDER_CLASSES)}"
        )
        sys.exit(2)
    if not provider_cls.is_available(coach_bin):
        console.print(
            f"[red]Coach provider '{chosen_provider}' CLI not found.[/] "
            f"Install: {provider_cls.install_hint}"
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
    MeetCoachApp(settings).run()


if __name__ == "__main__":
    cli()
