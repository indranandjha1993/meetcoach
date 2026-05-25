from __future__ import annotations

import shutil
import sys

import click
from rich.console import Console
from rich.table import Table

from meetcoach.audio import find_blackhole, find_default_mic, find_device, list_devices
from meetcoach.config import Settings

console = Console()


@click.group()
def cli() -> None:
    """Live meeting transcription + Claude coach."""


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

    claude_path = shutil.which(settings.claude_bin)
    if claude_path:
        console.print(f"[green]✓[/] claude CLI at {claude_path}")
    else:
        console.print(f"[red]✗[/] claude CLI not found (looked for {settings.claude_bin!r}).")
        ok = False

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
@click.option("--model", "coach_model", default=None, help="Override --model passed to claude -p.")
@click.option("--no-mic", is_flag=True, help="Skip mic capture (system audio only).")
@click.option("--no-system", is_flag=True, help="Skip system audio capture (mic only).")
def start(
    mic: str | None,
    system: str | None,
    engine: str,
    whisper_model: str,
    interval: float,
    coach_model: str | None,
    no_mic: bool,
    no_system: bool,
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

    settings = Settings(
        mic_device=mic_idx,
        system_device=sys_idx,
        engine=engine,
        whisper_model=whisper_model,
        coach_interval=interval,
        coach_model=coach_model,
    )
    MeetCoachApp(settings).run()


if __name__ == "__main__":
    cli()
