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
    speaker: str  # "you" for mic; "speaker-N" for diarized system audio
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

    async def reconnect(self) -> None:  # pragma: no cover
        """Close all in-flight connections; the feed loop will reopen as new audio arrives."""
        return None


class DeepgramTranscriber(Transcriber):
    """Deepgram Nova-3 streaming over raw websockets.

    One connection per audio source. The mic source (`you`) skips diarization
    — there's only ever one user speaking into it. The system source (`other`)
    has `diarize=true` so multiple remote speakers each get a stable
    `speaker-N` label from Deepgram's per-word speaker IDs.

    Final Results events with multiple speakers are split into one
    TranscriptEvent per contiguous same-speaker word run.
    """

    URL_BASE = "wss://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, sample_rate: int = 16000) -> None:
        try:
            import websockets  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "websockets not installed (install with: pip install websockets)"
            ) from e
        self.api_key = api_key
        self.sample_rate = sample_rate
        self._conns: dict[Speaker, object] = {}
        self._readers: dict[Speaker, asyncio.Task] = {}
        self._out: asyncio.Queue[TranscriptEvent] = asyncio.Queue()

    def _build_url(self, source: Speaker) -> str:
        params = [
            "model=nova-3",
            "language=multi",
            "encoding=linear16",
            f"sample_rate={self.sample_rate}",
            "smart_format=true",
            "punctuate=true",
            "interim_results=true",
            "endpointing=300",
        ]
        if source == "other":
            params.append("diarize=true")
        return f"{self.URL_BASE}?{'&'.join(params)}"

    async def _open(self, source: Speaker) -> object:
        import websockets

        url = self._build_url(source)
        headers = {"Authorization": f"Token {self.api_key}"}
        ws = await asyncio.wait_for(
            websockets.connect(url, additional_headers=headers),
            timeout=10,
        )
        reader = asyncio.create_task(self._read(source, ws))
        self._readers[source] = reader
        return ws

    async def _read(self, source: Speaker, ws) -> None:
        import websockets

        try:
            async for msg in ws:
                try:
                    raw = msg.encode() if isinstance(msg, str) else msg
                    data = json.loads(raw)
                except Exception:
                    continue
                if data.get("type") != "Results":
                    continue
                alt = (data.get("channel", {}).get("alternatives") or [{}])[0]
                transcript = (alt.get("transcript") or "").strip()
                if not transcript:
                    continue
                is_final = bool(data.get("is_final"))
                ts = time.time()

                if source == "you":
                    await self._out.put(TranscriptEvent("you", transcript, is_final, ts))
                    continue

                # System source: split by diarized speaker IDs
                words = alt.get("words") or []
                if not is_final or not words:
                    # Partials / no word info → attribute to the first speaker present
                    first = words[0].get("speaker", 0) if words else 0
                    await self._out.put(
                        TranscriptEvent(f"speaker-{first}", transcript, is_final, ts)
                    )
                    continue

                # Final result with words → emit one event per contiguous same-speaker run
                cur_spk: int | None = None
                cur_words: list[str] = []
                for w in words:
                    spk = int(w.get("speaker", 0))
                    txt = (w.get("punctuated_word") or w.get("word") or "").strip()
                    if not txt:
                        continue
                    if cur_spk is None:
                        cur_spk = spk
                    if spk != cur_spk:
                        text = " ".join(cur_words).strip()
                        if text:
                            await self._out.put(
                                TranscriptEvent(f"speaker-{cur_spk}", text, True, ts)
                            )
                        cur_spk = spk
                        cur_words = []
                    cur_words.append(txt)
                if cur_words and cur_spk is not None:
                    text = " ".join(cur_words).strip()
                    if text:
                        await self._out.put(
                            TranscriptEvent(f"speaker-{cur_spk}", text, True, ts)
                        )
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

    async def reconnect(self) -> None:
        """Drop both per-channel websockets. The next audio chunk re-opens them."""
        await self.close()


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

    async def _transcribe(self, source: Speaker, pcm: np.ndarray) -> None:
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
            label = "you" if source == "you" else "speaker-0"
            await self._out.put(
                TranscriptEvent(speaker=label, text=text, is_final=True, ts=time.time())
            )
