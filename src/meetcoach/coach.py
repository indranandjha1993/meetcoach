from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from meetcoach.config import Settings
from meetcoach.providers import CoachProvider, get_provider

COACH_SYSTEM = """\
You are a silent meeting coach watching a live transcript. The transcript is
multi-speaker. "You:" lines are spoken by the user you assist. "Other:" lines
are everyone else in the meeting.

Your job: surface only what is high-value for the user, in 1-3 short bullets:
- A direct question to them they haven't answered
- A factual claim that seems wrong or needs checking
- A point they should make next
- A commitment or action item they should capture

If nothing notable since the last check, respond with the literal word: PASS

Be terse. No headers. No restating what was said. No filler. Markdown bullets only.
"""

ASK_SYSTEM = """\
You are an on-demand assistant for a live meeting. The user will paste a
transcript and ask a question about it. Answer concisely and directly. Use
markdown if it helps. Cite a specific line if relevant. If you don't have
enough info, say so.
"""


class SpeakerLabeler:
    """Maps raw speaker IDs from the transcriber to human-readable labels.

    - "you" → mic_label (e.g. "Indranand" or "You")
    - "speaker-N" → next unused name from `names` (first-seen-wins),
      falling back to "Speaker-N" if names run out.
    """

    def __init__(self, mic_label: str = "You", names: list[str] | None = None) -> None:
        self.mic_label = mic_label
        self.names = list(names or [])
        self._mapped: dict[str, str] = {}

    def label(self, speaker: str) -> str:
        if speaker == "you":
            return self.mic_label
        if speaker in self._mapped:
            return self._mapped[speaker]
        if speaker.startswith("speaker-"):
            if len(self._mapped) < len(self.names):
                name = self.names[len(self._mapped)]
            else:
                n = speaker.removeprefix("speaker-")
                name = f"Speaker-{n}"
            self._mapped[speaker] = name
            return name
        return speaker.title()


@dataclass(slots=True)
class TranscriptLine:
    speaker: str
    text: str
    ts: float

    def format(self, labeler: SpeakerLabeler | None = None) -> str:
        t = time.strftime("%H:%M:%S", time.localtime(self.ts))
        if labeler is not None:
            label = labeler.label(self.speaker)
        elif self.speaker == "you":
            label = "You"
        elif self.speaker.startswith("speaker-"):
            label = f"Speaker-{self.speaker.removeprefix('speaker-')}"
        else:
            label = self.speaker.title()
        return f"[{t}] {label}: {self.text}"


@dataclass(slots=True)
class Coach:
    settings: Settings
    on_suggestion: Callable[[str], None]
    on_ask_reply: Callable[[str, str], None]
    lines: list[TranscriptLine] = field(default_factory=list)
    transcript_path: Path | None = None
    labeler: SpeakerLabeler | None = None
    provider: CoachProvider | None = None
    _last_sent_idx: int = 0
    _stable_idx: int = 0
    _last_stable_refresh: float = field(default_factory=time.time)
    _paused: bool = False
    _tick_task: asyncio.Task | None = None
    _inflight: bool = False

    def __post_init__(self) -> None:
        self.settings.transcript_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.transcript_path = self.settings.transcript_dir / f"meeting-{ts}.txt"
        self.transcript_path.touch()

        stable_dir = Path.home() / ".meetcoach"
        stable_dir.mkdir(parents=True, exist_ok=True)
        stable_link = stable_dir / "current.txt"
        with contextlib.suppress(FileNotFoundError):
            stable_link.unlink()
        stable_link.symlink_to(self.transcript_path)

        if self.labeler is None:
            self.labeler = SpeakerLabeler(
                mic_label=self.settings.mic_label,
                names=self.settings.names,
            )

        if self.provider is None:
            self.provider = get_provider(
                name=self.settings.coach_provider,
                binary=self.settings.coach_bin,
                model=self.settings.coach_model,
            )

    def set_provider(self, provider: CoachProvider) -> None:
        """Swap the active coach provider at runtime (used by TUI [s] hotkey)."""
        self.provider = provider

    def add_line(self, speaker: str, text: str) -> None:
        line = TranscriptLine(speaker=speaker, text=text, ts=time.time())
        self.lines.append(line)
        if self.transcript_path:
            with self.transcript_path.open("a") as f:
                f.write(line.format(self.labeler) + "\n")

    def toggle_pause(self) -> bool:
        self._paused = not self._paused
        return self._paused

    async def start(self) -> None:
        self._tick_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._tick_task:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._tick_task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.coach_interval)
            if self._paused or self._inflight:
                continue
            new_lines = self.lines[self._last_sent_idx :]
            new_chars = sum(len(line.text) for line in new_lines)
            if new_chars < self.settings.coach_min_new_chars:
                continue
            await self._tick()

    async def _tick(self) -> None:
        self._inflight = True
        try:
            if time.time() - self._last_stable_refresh > 240:
                self._stable_idx = max(0, len(self.lines) - 4)
                self._last_stable_refresh = time.time()

            stable = "\n".join(line.format(self.labeler) for line in self.lines[: self._stable_idx]) or "(none yet)"
            fresh = "\n".join(line.format(self.labeler) for line in self.lines[self._stable_idx :])
            self._last_sent_idx = len(self.lines)

            prompt = (
                f"<transcript_so_far>\n{stable}\n</transcript_so_far>\n\n"
                f"<new_since_last_check>\n{fresh}\n</new_since_last_check>\n\n"
                "Apply your rules. Respond now."
            )
            reply = await self._invoke(COACH_SYSTEM, prompt)
            if reply and reply.strip().upper() != "PASS":
                self.on_suggestion(reply.strip())
        finally:
            self._inflight = False

    async def ask(self, question: str) -> None:
        full = "\n".join(line.format(self.labeler) for line in self.lines) or "(no transcript yet)"
        prompt = (
            f"<transcript>\n{full}\n</transcript>\n\n"
            f"<question>\n{question}\n</question>"
        )
        reply = await self._invoke(ASK_SYSTEM, prompt)
        self.on_ask_reply(question, reply.strip() if reply else "(no response)")

    async def _invoke(self, system: str, prompt: str) -> str:
        if self.provider is None:
            return "[error: no coach provider configured]"
        return await self.provider.invoke(system, prompt, timeout_s=60)
