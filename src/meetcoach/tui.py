from __future__ import annotations

import asyncio
import time
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from meetcoach.audio import DualCapture
from meetcoach.coach import Coach
from meetcoach.config import Settings
from meetcoach.stt import DeepgramTranscriber, Transcriber, TranscriptEvent, WhisperTranscriber


class StatusBar(Static):
    pass


class MeetCoachApp(App):
    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; background: $boost; color: $text; padding: 0 1; }
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
        Binding("p", "toggle_pause", "Pause coach"),
        Binding("c", "clear_coach", "Clear coach pane"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.capture: DualCapture | None = None
        self.transcriber: Transcriber | None = None
        self.coach: Coach | None = None
        self._partials: dict[str, str] = {"you": "", "other": ""}
        self._bg: set[asyncio.Task] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
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
        self.set_status(f"engine={self.settings.resolve_engine()} | mic={self.settings.mic_device} | system={self.settings.system_device}")
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
        # Partials are noisy; surface briefly in the status bar instead of the log.
        you = self._partials.get("you", "")
        other = self._partials.get("other", "")
        parts = []
        if you:
            parts.append(f"you… {you[-60:]}")
        if other:
            parts.append(f"other… {other[-60:]}")
        if parts:
            self.set_status(" | ".join(parts))

    def _log_final(self, ev: TranscriptEvent) -> None:
        t = time.strftime("%H:%M:%S", time.localtime(ev.ts))
        color = "cyan" if ev.speaker == "you" else "yellow"
        label = "You" if ev.speaker == "you" else "Other"
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
