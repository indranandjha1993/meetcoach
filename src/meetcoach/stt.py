from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np

from meetcoach.audio import AudioChunk, Speaker


@dataclass(slots=True)
class TranscriptEvent:
    speaker: Speaker
    text: str
    is_final: bool
    ts: float


class Transcriber:
    async def run(
        self, audio_q: asyncio.Queue[AudioChunk]
    ) -> AsyncIterator[TranscriptEvent]:  # pragma: no cover
        raise NotImplementedError
        yield  # type: ignore[unreachable]

    async def close(self) -> None:  # pragma: no cover
        return None


class DeepgramTranscriber(Transcriber):
    """Deepgram Flux v2 streaming over raw websockets.

    One connection per speaker channel; Flux emits Update events with the
    growing transcript per turn and EndOfTurn when the speaker pauses.
    We map Update→partial, EndOfTurn→final.
    """

    URL_TEMPLATE = (
        "wss://api.deepgram.com/v2/listen"
        "?eot_threshold=0.7"
        "&eot_timeout_ms=2000"
        "&model=flux-general-multi"
        "&encoding=linear16"
        "&sample_rate={sr}"
    )

    def __init__(self, api_key: str, sample_rate: int = 16000) -> None:
        try:
            import websockets  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "websockets not installed (install with: pip install 'meetcoach[deepgram]')"
            ) from e
        self.api_key = api_key
        self.sample_rate = sample_rate
        self._conns: dict[Speaker, object] = {}
        self._readers: dict[Speaker, asyncio.Task] = {}
        self._out: asyncio.Queue[TranscriptEvent] = asyncio.Queue()
        self._latest: dict[Speaker, dict[int, str]] = {"you": {}, "other": {}}

    async def _open(self, speaker: Speaker) -> object:
        import websockets

        url = self.URL_TEMPLATE.format(sr=self.sample_rate)
        headers = {"Authorization": f"Token {self.api_key}"}
        ws = await asyncio.wait_for(
            websockets.connect(url, additional_headers=headers),
            timeout=10,
        )
        reader = asyncio.create_task(self._read(speaker, ws))
        self._readers[speaker] = reader
        return ws

    async def _read(self, speaker: Speaker, ws) -> None:
        import websockets

        try:
            async for msg in ws:
                try:
                    raw = msg.encode() if isinstance(msg, str) else msg
                    data = json.loads(raw)
                except Exception:
                    continue
                evt = data.get("event") or data.get("type") or ""
                ti = data.get("turn_index")
                tr = (data.get("transcript") or "").strip()
                if evt == "Update":
                    if ti is not None and tr:
                        self._latest[speaker][ti] = tr
                        await self._out.put(
                            TranscriptEvent(speaker, tr, False, time.time())
                        )
                elif evt == "EndOfTurn":
                    final_text = self._latest[speaker].pop(ti, tr) if ti is not None else tr
                    if final_text:
                        await self._out.put(
                            TranscriptEvent(speaker, final_text, True, time.time())
                        )
                # StartOfTurn, Connected, Error: ignore silently (debug-only)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            pass

    async def run(self, audio_q: asyncio.Queue[AudioChunk]) -> AsyncIterator[TranscriptEvent]:
        feeder = asyncio.create_task(self._feed(audio_q))
        try:
            while True:
                ev = await self._out.get()
                yield ev
        finally:
            feeder.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await feeder

    async def _feed(self, audio_q: asyncio.Queue[AudioChunk]) -> None:
        while True:
            chunk = await audio_q.get()
            conn = self._conns.get(chunk.speaker)
            if conn is None:
                try:
                    conn = await self._open(chunk.speaker)
                except Exception:
                    await asyncio.sleep(1.0)
                    continue
                self._conns[chunk.speaker] = conn
            try:
                await conn.send(chunk.pcm.tobytes())  # type: ignore[attr-defined]
            except Exception:
                self._conns.pop(chunk.speaker, None)
                reader = self._readers.pop(chunk.speaker, None)
                if reader:
                    reader.cancel()

    async def close(self) -> None:
        readers = list(self._readers.values())
        conns = list(self._conns.values())
        self._readers.clear()
        self._conns.clear()
        for reader in readers:
            reader.cancel()
        for conn in conns:
            with contextlib.suppress(Exception):
                await conn.close()  # type: ignore[attr-defined]


class WhisperTranscriber(Transcriber):
    """Local fallback. Buffers per speaker, flushes on silence or max window."""

    SILENCE_MS = 700
    MAX_WINDOW_S = 6.0

    def __init__(self, model_name: str = "small.en", sample_rate: int = 16000) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper not installed. Reinstall with: pip install 'meetcoach[whisper]'"
            ) from e
        try:
            import webrtcvad  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "webrtcvad not installed. Reinstall with: pip install 'meetcoach[whisper]'"
            ) from e
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_name, device="auto", compute_type="auto")
        self.sample_rate = sample_rate
        self._out: asyncio.Queue[TranscriptEvent] = asyncio.Queue()
        self._jobs: set[asyncio.Task] = set()

    async def run(self, audio_q: asyncio.Queue[AudioChunk]) -> AsyncIterator[TranscriptEvent]:
        import webrtcvad

        vad = webrtcvad.Vad(2)
        buffers: dict[Speaker, list[np.ndarray]] = {"you": [], "other": []}
        silence_ms: dict[Speaker, int] = {"you": 0, "other": 0}
        feeder = asyncio.create_task(self._consume(audio_q, vad, buffers, silence_ms))
        try:
            while True:
                ev = await self._out.get()
                yield ev
        finally:
            feeder.cancel()

    async def _consume(
        self,
        audio_q: asyncio.Queue[AudioChunk],
        vad,
        buffers: dict[Speaker, list[np.ndarray]],
        silence_ms: dict[Speaker, int],
    ) -> None:
        frame_ms = 30
        frame_samples = self.sample_rate * frame_ms // 1000
        while True:
            chunk = await audio_q.get()
            pcm = chunk.pcm
            buffers[chunk.speaker].append(pcm)

            for i in range(0, len(pcm) - frame_samples + 1, frame_samples):
                frame = pcm[i : i + frame_samples].tobytes()
                try:
                    speech = vad.is_speech(frame, self.sample_rate)
                except Exception:
                    speech = True
                if speech:
                    silence_ms[chunk.speaker] = 0
                else:
                    silence_ms[chunk.speaker] += frame_ms

            total = sum(b.size for b in buffers[chunk.speaker])
            window_s = total / self.sample_rate

            should_flush = (
                silence_ms[chunk.speaker] >= self.SILENCE_MS and window_s > 0.5
            ) or window_s >= self.MAX_WINDOW_S
            if should_flush:
                audio = np.concatenate(buffers[chunk.speaker])
                buffers[chunk.speaker].clear()
                silence_ms[chunk.speaker] = 0
                job = asyncio.create_task(self._transcribe(chunk.speaker, audio))
                self._jobs.add(job)
                job.add_done_callback(self._jobs.discard)

    async def _transcribe(self, speaker: Speaker, pcm: np.ndarray) -> None:
        floats = pcm.astype(np.float32) / 32768.0
        loop = asyncio.get_running_loop()

        def work() -> str:
            segs, _ = self.model.transcribe(
                floats,
                language="en",
                vad_filter=False,
                beam_size=1,
                condition_on_previous_text=False,
            )
            return " ".join(s.text.strip() for s in segs).strip()

        text = await loop.run_in_executor(None, work)
        if text:
            await self._out.put(
                TranscriptEvent(speaker=speaker, text=text, is_final=True, ts=time.time())
            )
