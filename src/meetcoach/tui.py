from __future__ import annotations

import asyncio
import contextlib
import time
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
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
        Binding("a", "ask", "Ask Claude"),
        Binding("m", "toggle_mic", "Mute mic"),
        Binding("p", "toggle_pause", "Pause coach"),
        Binding("c", "clear_coach", "Clear coach pane"),
        Binding("question_mark", "show_detail", "Status detail"),
        Binding("q", "quit", "Quit"),
    ]

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
        # Run the full capability scan once; show modal if anything is broken.
        caps = check_all(self.settings)
        self._refresh_capbar(caps)
        broken = broken_capabilities(caps)
        if broken:
            choice = await self.push_screen_wait(ReadinessModal(broken))
            if choice == "quit":
                self.exit()
                return
            if choice == "detail":
                await self.push_screen_wait(CapabilityDetailScreen(caps))

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
