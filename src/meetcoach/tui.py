from __future__ import annotations

import asyncio
import contextlib
import subprocess
import time
from functools import partial
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from meetcoach.audio import DualCapture
from meetcoach.capabilities import (
    Capability,
    CapabilityState,
    broken_capabilities,
    check_all,
    group_by,
)
from meetcoach.coach import Coach
from meetcoach.config import Settings
from meetcoach.providers import PROVIDER_CLASSES, get_provider
from meetcoach.stt import DeepgramTranscriber, Transcriber, TranscriptEvent, WhisperTranscriber

_GROUP_TITLES = {
    "audio": "Audio",
    "transcription": "STT",
    "coach": "Coach",
    "mcp": "MCP",
    "slash-commands": "/meeting",
}

_STATE_COLOR = {
    CapabilityState.OK: "green",
    CapabilityState.DEGRADED: "yellow",
    CapabilityState.BROKEN: "red",
    CapabilityState.DISABLED: "grey50",
}

_STATE_GLYPH = {
    CapabilityState.OK: "✓",
    CapabilityState.DEGRADED: "!",
    CapabilityState.BROKEN: "✗",
    CapabilityState.DISABLED: "-",
}


def _worst_state(caps: list[Capability]) -> CapabilityState:
    """Aggregate the worst state across a group (BROKEN > DEGRADED > DISABLED > OK)."""
    order = [CapabilityState.BROKEN, CapabilityState.DEGRADED, CapabilityState.OK, CapabilityState.DISABLED]
    states = {c.state for c in caps}
    for s in order:
        if s in states:
            return s
    return CapabilityState.OK


class StatusBar(Static):
    pass


class CapabilityBar(Static):
    """One-line status bar: colored dot per capability group + recording info."""

    def render_caps(self, caps: list[Capability], recording_path: str | None) -> None:
        groups = group_by(caps)
        parts = []
        for grp in ("audio", "transcription", "coach", "mcp"):
            items = [c for c in groups.get(grp, []) if c.state != CapabilityState.DISABLED]
            state = _worst_state(items) if items else CapabilityState.DISABLED
            color = _STATE_COLOR[state]
            label = _GROUP_TITLES[grp]
            parts.append(f"[{color}]●[/] {label}")
        line = "   ".join(parts)
        if recording_path:
            line += f"   [dim]│   rec → {recording_path}[/]"
        line += "   [dim]│   [?] details[/]"
        self.update(line)


class ReadinessModal(ModalScreen[str]):
    """Shown on startup if any capability is BROKEN. User picks: continue / detail / quit."""

    CSS = """
    ReadinessModal { align: center middle; }
    #modal-body {
        width: 80%; max-width: 100; height: auto; max-height: 80%;
        background: $panel; border: round $warning; padding: 1 2;
    }
    #modal-body Static.problem-name { color: $error; padding-top: 1; }
    #modal-body Static.problem-detail { color: $text-muted; padding-left: 2; }
    #modal-body Static.problem-step { color: $secondary; padding-left: 4; }
    #modal-buttons { height: auto; align-horizontal: center; padding-top: 1; }
    #modal-buttons Button { margin: 0 1; }
    """

    def __init__(self, broken: list[Capability]) -> None:
        super().__init__()
        self.broken = broken

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="modal-body"):
            yield Static(
                f"[bold yellow]{len(self.broken)} capabilit"
                f"{'y' if len(self.broken) == 1 else 'ies'} need"
                f"{'s' if len(self.broken) == 1 else ''} attention[/]"
            )
            yield Static("")
            for c in self.broken:
                yield Static(f"✗ [bold]{c.name}[/]", classes="problem-name")
                yield Static(c.detail, classes="problem-detail")
                if c.fix_steps:
                    yield Static("To fix:", classes="problem-detail")
                    for step in c.fix_steps:
                        yield Static(step, classes="problem-step")
            yield Static("")
            with Horizontal(id="modal-buttons"):
                yield Button("Continue anyway", id="continue", variant="warning")
                yield Button("Show full status", id="detail")
                yield Button("Quit", id="quit", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "continue")


class CapabilityDetailScreen(Screen):
    """Toggleable full breakdown of every capability, with state + detail + fix steps."""

    BINDINGS: ClassVar = [Binding("escape", "dismiss", "Close"), Binding("question_mark", "dismiss", "Close")]

    CSS = """
    CapabilityDetailScreen { layout: vertical; }
    #detail-body { padding: 1 2; }
    #detail-body Static.group-title { color: $accent; padding-top: 1; }
    #detail-body Static.cap-line { padding-left: 2; }
    #detail-body Static.cap-fix { color: $secondary; padding-left: 6; }
    #detail-footer { dock: bottom; height: 1; background: $boost; padding: 0 1; }
    """

    def __init__(self, caps: list[Capability]) -> None:
        super().__init__()
        self.caps = caps

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="detail-body"):
            for grp, title in _GROUP_TITLES.items():
                items = group_by(self.caps).get(grp, [])
                if not items:
                    continue
                yield Static(f"[bold]{title}[/]", classes="group-title")
                for c in items:
                    glyph = _STATE_GLYPH[c.state]
                    color = _STATE_COLOR[c.state]
                    yield Static(
                        f"[{color}]{glyph}[/] {c.name:38s}  [dim]{c.detail}[/]",
                        classes="cap-line",
                    )
                    if c.fix_steps and c.state != CapabilityState.OK:
                        for step in c.fix_steps:
                            yield Static(step, classes="cap-fix")
        yield Static("[dim]Press Esc or ? to close[/]", id="detail-footer")

    def action_dismiss(self) -> None:
        self.app.pop_screen()


class MeetcoachCommands(Provider):
    """Command-palette entries that mirror the CLI / hotkeys.

    The user can also bind palette shortcuts to scripted defaults; the source of
    truth for "what actions exist" stays in this provider.
    """

    @property
    def _meetcoach_app(self) -> MeetCoachApp:
        return self.app  # type: ignore[return-value]

    def _all_commands(self) -> list[tuple[str, str, str]]:
        app = self._meetcoach_app
        coach_name = app.coach.provider.name if (app.coach and app.coach.provider) else "?"
        cmds: list[tuple[str, str, str]] = [
            ("Ask Claude…", "Open the Ask input (also: [a])", "ask"),
            (
                f"Switch coach provider (current: {coach_name})",
                "Cycle through installed Claude / Gemini / Codex CLIs (also: [s])",
                "switch_coach",
            ),
            ("Reconnect Deepgram", "Drop + reopen the STT websockets (also: [r])", "reconnect_stt"),
            ("Toggle mic mute", "Stop the mic stream from being transcribed (also: [m])", "toggle_mic"),
            ("Pause / resume auto coach", "Stop the 25s coach tick (also: [p])", "toggle_pause"),
            ("Show status detail", "Full breakdown of every capability (also: [?])", "show_detail"),
            ("Re-run capability scan", "Refresh the top-bar indicators now", "refresh_caps"),
            ("Clear coach pane", "Wipe the right-hand pane (also: [c])", "clear_coach"),
            ("Open transcripts folder", "Reveal recorded transcripts in Finder", "open_transcripts"),
            ("Copy MCP config snippet", "Print the meetcoach MCP JSON to copy into ~/.claude/settings.json", "copy_mcp_config"),
            ("Show /meeting prompt", "Print the prompt body for paste into any LLM tool", "show_prompt"),
            ("Reinstall /meeting handler", "Re-run scripts/install-slash-commands.sh", "reinstall_slash"),
            ("Quit", "Exit meetcoach (also: [q])", "quit"),
        ]
        return cmds

    async def discover(self) -> Hits:
        for title, help_text, action_name in self._all_commands():
            yield DiscoveryHit(
                title,
                partial(self._run_action, action_name),
                help=help_text,
            )

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for title, help_text, action_name in self._all_commands():
            score = matcher.match(title)
            if score:
                yield Hit(
                    score,
                    matcher.highlight(title),
                    partial(self._run_action, action_name),
                    help=help_text,
                )

    def _run_action(self, action_name: str) -> None:
        action = getattr(self._meetcoach_app, f"action_{action_name}", None)
        if action is None:
            return
        result = action()
        if asyncio.iscoroutine(result):
            self._meetcoach_app._spawn(result)


class MeetCoachApp(App):
    CSS = """
    Screen { layout: vertical; }
    #capbar { height: 1; background: $boost; color: $text; padding: 0 1; }
    #status { height: 1; background: $boost; color: $text-muted; padding: 0 1; }
    #panes { height: 1fr; }
    #transcript-pane { width: 60%; border: solid $accent; padding: 0 1; }
    #coach-pane { width: 40%; border: solid $success; padding: 0 1; }
    #transcript-pane > .pane-title, #coach-pane > .pane-title { color: $text-muted; }
    #ask-row { height: 3; display: none; }
    #ask-row.visible { display: block; }
    RichLog { height: 1fr; }
    """

    BINDINGS: ClassVar = [
        Binding("a", "ask", "Ask"),
        Binding("m", "toggle_mic", "Mute mic"),
        Binding("s", "switch_coach", "Switch coach"),
        Binding("r", "reconnect_stt", "Reconnect STT"),
        Binding("p", "toggle_pause", "Pause coach"),
        Binding("c", "clear_coach", "Clear coach"),
        Binding("question_mark", "show_detail", "Status"),
        Binding("q", "quit", "Quit"),
    ]

    COMMANDS = App.COMMANDS | {MeetcoachCommands}

    SPEAKER_PALETTE: ClassVar = ["yellow", "green", "magenta", "blue", "red", "orange1"]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.capture: DualCapture | None = None
        self.transcriber: Transcriber | None = None
        self.coach: Coach | None = None
        self._partials: dict[str, str] = {}
        self._bg: set[asyncio.Task] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield CapabilityBar("checking capabilities...", id="capbar")
        yield StatusBar("starting...", id="status")
        with Horizontal(id="panes"):
            with Vertical(id="transcript-pane"):
                yield Static("[b]Transcript[/]", classes="pane-title")
                yield RichLog(id="transcript-log", wrap=True, highlight=False, markup=True)
            with Vertical(id="coach-pane"):
                yield Static("[b]Coach[/]", classes="pane-title")
                yield RichLog(id="coach-log", wrap=True, highlight=False, markup=True)
        with Container(id="ask-row"):
            yield Input(placeholder="Ask Claude about the meeting... (Enter to send, Esc to cancel)", id="ask-input")
        yield Footer()

    async def on_mount(self) -> None:
        # Capability scan: surface red items via a non-blocking modal so the
        # pipeline can start in parallel. push_screen_wait() can't be used
        # from on_mount (requires a Textual worker), so we use the callback
        # form instead.
        caps = check_all(self.settings)
        self._refresh_capbar(caps)
        broken = broken_capabilities(caps)
        if broken:
            def on_modal_choice(choice: str | None) -> None:
                if choice == "quit":
                    self.exit()
                elif choice == "detail":
                    self.push_screen(CapabilityDetailScreen(caps))
            self.push_screen(ReadinessModal(broken), on_modal_choice)

        # Refresh the cap bar every 5s so post-launch fixes show up live.
        self.set_interval(5.0, self._tick_caps)

        self.set_status(
            f"engine={self.settings.resolve_engine()} | mic={self.settings.mic_device} | system={self.settings.system_device}"
        )
        try:
            await self._start_pipeline()
        except Exception as e:
            self._log_coach(f"[red]Startup error:[/] {e}")
            self.set_status(f"ERROR: {e}")

    async def _start_pipeline(self) -> None:
        self.capture = DualCapture(
            mic_device=self.settings.mic_device if isinstance(self.settings.mic_device, int) else None,
            system_device=self.settings.system_device if isinstance(self.settings.system_device, int) else None,
            sample_rate=self.settings.sample_rate,
        )
        audio_q = await self.capture.start()

        engine = self.settings.resolve_engine()
        if engine == "deepgram":
            if not self.settings.deepgram_key:
                raise RuntimeError("DEEPGRAM_API_KEY not set")
            self.transcriber = DeepgramTranscriber(self.settings.deepgram_key, self.settings.sample_rate)
        elif engine == "whisper":
            self._log_coach("[dim]Loading faster-whisper model... (first run may download weights)[/]")
            self.transcriber = WhisperTranscriber(self.settings.whisper_model, self.settings.sample_rate)
        else:
            raise RuntimeError(f"unknown engine {engine!r}")

        self.coach = Coach(
            settings=self.settings,
            on_suggestion=self._on_suggestion,
            on_ask_reply=self._on_ask_reply,
        )
        await self.coach.start()
        if self.coach.transcript_path:
            self.set_status(
                f"engine={engine} | recording → {self.coach.transcript_path.name}"
            )
        self._spawn(self._consume(audio_q))

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    async def _consume(self, audio_q) -> None:
        assert self.transcriber is not None
        async for ev in self.transcriber.run(audio_q):
            self._handle_event(ev)

    def _handle_event(self, ev: TranscriptEvent) -> None:
        if ev.is_final:
            self._partials[ev.speaker] = ""
            self._log_final(ev)
            if self.coach:
                self.coach.add_line(ev.speaker, ev.text)
        else:
            self._partials[ev.speaker] = ev.text
            self._render_partials()

    def _render_partials(self) -> None:
        # Partials are noisy; surface briefly in the status bar.
        labeler = self.coach.labeler if self.coach else None
        parts = []
        for spk, text in self._partials.items():
            if not text:
                continue
            label = labeler.label(spk) if labeler else spk
            parts.append(f"{label}… {text[-60:]}")
        if parts:
            self.set_status(" | ".join(parts))

    def _color_for(self, speaker: str) -> str:
        if speaker == "you":
            return "cyan"
        if speaker.startswith("speaker-"):
            try:
                n = int(speaker.removeprefix("speaker-"))
            except ValueError:
                n = 0
            return self.SPEAKER_PALETTE[n % len(self.SPEAKER_PALETTE)]
        return "white"

    def _log_final(self, ev: TranscriptEvent) -> None:
        t = time.strftime("%H:%M:%S", time.localtime(ev.ts))
        color = self._color_for(ev.speaker)
        labeler = self.coach.labeler if self.coach else None
        label = labeler.label(ev.speaker) if labeler else ev.speaker
        log = self.query_one("#transcript-log", RichLog)
        log.write(f"[dim]{t}[/] [{color}]{label}:[/] {ev.text}")

    def _on_suggestion(self, text: str) -> None:
        t = time.strftime("%H:%M:%S")
        self._log_coach(f"[dim]{t}[/] {text}")

    def _on_ask_reply(self, question: str, reply: str) -> None:
        self._log_coach(f"[b magenta]Q:[/] {question}")
        self._log_coach(f"[green]A:[/] {reply}")
        self._log_coach("")

    def _log_coach(self, text: str) -> None:
        log = self.query_one("#coach-log", RichLog)
        log.write(text)

    def set_status(self, text: str) -> None:
        self.query_one("#status", StatusBar).update(text)

    def _refresh_capbar(self, caps: list[Capability] | None = None) -> None:
        if caps is None:
            caps = check_all(self.settings)
        rec = self.coach.transcript_path.name if (self.coach and self.coach.transcript_path) else None
        with contextlib.suppress(Exception):
            self.query_one("#capbar", CapabilityBar).render_caps(caps, rec)

    def _tick_caps(self) -> None:
        self._refresh_capbar()

    def action_show_detail(self) -> None:
        caps = check_all(self.settings)
        self.push_screen(CapabilityDetailScreen(caps))

    def action_switch_coach(self) -> None:
        """Cycle the auto-coach to the next installed provider."""
        if not self.coach:
            self._log_coach("[dim]coach not running yet[/]")
            return
        available = [
            name for name, cls in PROVIDER_CLASSES.items()
            if cls.is_available(self.settings.coach_bin if name == self.settings.coach_provider else None)
        ]
        if not available:
            self._log_coach("[red]no coach providers installed — nothing to switch to[/]")
            return
        current = self.coach.provider.name if self.coach.provider else available[0]
        if current in available:
            next_name = available[(available.index(current) + 1) % len(available)]
        else:
            next_name = available[0]
        if next_name == current and len(available) == 1:
            self._log_coach(f"[dim]only {current} is installed — nothing to switch to[/]")
            return
        new_provider = get_provider(next_name, model=self.settings.coach_model)
        self.coach.set_provider(new_provider)
        self.settings.coach_provider = next_name
        self._log_coach(f"[dim]coach provider: {current} → {next_name}[/]")
        self._refresh_capbar()

    def action_reconnect_stt(self) -> None:
        """Drop and re-open the Deepgram websockets. Useful if a stream dies."""
        if not self.transcriber:
            self._log_coach("[dim]transcriber not running[/]")
            return
        self._log_coach("[dim]reconnecting Deepgram…[/]")
        self._spawn(self._do_reconnect())

    async def _do_reconnect(self) -> None:
        if not self.transcriber:
            return
        with contextlib.suppress(Exception):
            await self.transcriber.reconnect()
        self._log_coach("[dim]Deepgram reconnect requested — streams reopen on next audio[/]")
        self._refresh_capbar()

    def action_refresh_caps(self) -> None:
        self._refresh_capbar()
        self._log_coach("[dim]capability scan refreshed[/]")

    def action_open_transcripts(self) -> None:
        path = self.settings.transcript_dir
        path.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Exception):
            subprocess.Popen(["open", str(path)])
        self._log_coach(f"[dim]opened transcripts folder: {path}[/]")

    def action_copy_mcp_config(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        bin_path = repo_root / ".venv" / "bin" / "meetcoach-mcp"
        snippet = (
            "{\n"
            '  "mcpServers": {\n'
            '    "meetcoach": {\n'
            f'      "command": "{bin_path}",\n'
            '      "args": []\n'
            "    }\n"
            "  }\n"
            "}"
        )
        with contextlib.suppress(Exception):
            subprocess.run(["pbcopy"], input=snippet.encode(), check=True)
            self._log_coach("[green]✓[/] MCP config copied to clipboard. Paste into ~/.claude/settings.json")
            return
        self._log_coach("[yellow]could not copy to clipboard — here's the snippet:[/]")
        for line in snippet.splitlines():
            self._log_coach(f"  {line}")

    def action_show_prompt(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        prompt_path = repo_root / "share" / "skills" / "meeting" / "SKILL.md"
        if not prompt_path.exists():
            self._log_coach(f"[red]prompt missing at {prompt_path}[/]")
            return
        text = prompt_path.read_text(encoding="utf-8")
        # Strip frontmatter
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end != -1:
                text = text[end + 5 :].lstrip()
        with contextlib.suppress(Exception):
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            self._log_coach("[green]✓[/] /meeting prompt copied to clipboard")
            return
        self._log_coach("[dim]/meeting prompt:[/]")
        self._log_coach(text[:500] + ("..." if len(text) > 500 else ""))

    def action_reinstall_slash(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        installer = repo_root / "scripts" / "install-slash-commands.sh"
        if not installer.exists():
            self._log_coach(f"[red]installer not found at {installer}[/]")
            return
        try:
            out = subprocess.run(
                ["bash", str(installer)], capture_output=True, text=True, timeout=10
            )
            for line in out.stdout.splitlines():
                self._log_coach(f"[dim]{line}[/]")
            if out.returncode != 0:
                self._log_coach(f"[red]installer exit {out.returncode}[/]")
        except Exception as e:
            self._log_coach(f"[red]installer failed: {e}[/]")
        self._refresh_capbar()

    def action_ask(self) -> None:
        row = self.query_one("#ask-row", Container)
        row.add_class("visible")
        inp = self.query_one("#ask-input", Input)
        inp.value = ""
        inp.focus()

    def action_clear_coach(self) -> None:
        self.query_one("#coach-log", RichLog).clear()

    def action_toggle_pause(self) -> None:
        if not self.coach:
            return
        paused = self.coach.toggle_pause()
        self._log_coach(f"[dim]coach {'paused' if paused else 'resumed'}[/]")

    def action_toggle_mic(self) -> None:
        if not self.capture or self.capture.mic_device is None:
            self._log_coach("[dim]no mic device configured; nothing to toggle[/]")
            return
        muted = self.capture.toggle_mic()
        self._log_coach(f"[dim]mic {'muted (listen-only)' if muted else 'live'}[/]")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "ask-input":
            return
        question = event.value.strip()
        self.query_one("#ask-row", Container).remove_class("visible")
        if question and self.coach:
            self._log_coach("[dim]asking...[/]")
            self._spawn(self.coach.ask(question))

    def on_key(self, event) -> None:
        if event.key == "escape":
            row = self.query_one("#ask-row", Container)
            if row.has_class("visible"):
                row.remove_class("visible")

    async def on_unmount(self) -> None:
        import contextlib

        if self.capture:
            with contextlib.suppress(Exception):
                await self.capture.stop()
        if self.transcriber:
            with contextlib.suppress(Exception):
                await self.transcriber.close()
        if self.coach:
            with contextlib.suppress(Exception):
                await self.coach.stop()
        for task in list(self._bg):
            task.cancel()
        if self._bg:
            with contextlib.suppress(Exception):
                await asyncio.gather(*self._bg, return_exceptions=True)
