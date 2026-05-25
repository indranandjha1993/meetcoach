from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Literal

import numpy as np
import sounddevice as sd

Speaker = Literal["you", "other"]


@dataclass(slots=True)
class AudioChunk:
    speaker: Speaker
    pcm: np.ndarray  # mono int16
    sample_rate: int


def list_devices() -> list[dict]:
    return [
        {
            "index": i,
            "name": d["name"],
            "max_input_channels": d["max_input_channels"],
            "default_samplerate": d["default_samplerate"],
        }
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


def find_device(query: str | int | None) -> int | None:
    if query is None:
        return None
    if isinstance(query, int):
        return query
    needle = query.lower()
    for d in list_devices():
        if needle in d["name"].lower():
            return d["index"]
    raise ValueError(f"No input device matching {query!r}")


def find_blackhole() -> int | None:
    for d in list_devices():
        if "blackhole" in d["name"].lower():
            return d["index"]
    return None


def find_default_mic() -> int | None:
    try:
        default_in, _ = sd.default.device
        if default_in is not None and default_in >= 0:
            return int(default_in)
    except Exception:
        pass
    for d in list_devices():
        if "macbook" in d["name"].lower() and "microphone" in d["name"].lower():
            return d["index"]
    devs = list_devices()
    return devs[0]["index"] if devs else None


class DualCapture:
    """Open two parallel input streams (mic + system loopback) and post
    AudioChunks directly to an asyncio queue from sounddevice's callback
    threads via call_soon_threadsafe."""

    def __init__(
        self,
        mic_device: int | None,
        system_device: int | None,
        sample_rate: int = 16000,
        block_ms: int = 100,
    ) -> None:
        self.mic_device = mic_device
        self.system_device = system_device
        self.sample_rate = sample_rate
        self.block_size = sample_rate * block_ms // 1000
        self._streams: list[sd.InputStream] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_q: asyncio.Queue[AudioChunk] | None = None

    def _make_callback(self, speaker: Speaker):
        def cb(indata, _frames, _time, _status):
            mono = indata.mean(axis=1) if indata.ndim > 1 else indata
            pcm = np.clip(mono * 32767, -32768, 32767).astype(np.int16).copy()
            chunk = AudioChunk(speaker, pcm, self.sample_rate)
            loop = self._loop
            q = self._async_q
            if loop is None or q is None:
                return
            def _put() -> None:
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(chunk)
            loop.call_soon_threadsafe(_put)

        return cb

    async def start(self) -> asyncio.Queue[AudioChunk]:
        self._loop = asyncio.get_running_loop()
        self._async_q = asyncio.Queue(maxsize=200)

        device_channels = {"you": self.mic_device, "other": self.system_device}
        for speaker, device in device_channels.items():
            if device is None:
                continue
            info = sd.query_devices(device)
            channels = max(1, min(2, info["max_input_channels"]))
            self._streams.append(
                sd.InputStream(
                    device=device,
                    channels=channels,
                    samplerate=self.sample_rate,
                    blocksize=self.block_size,
                    dtype="float32",
                    callback=self._make_callback(speaker),
                )
            )
        if not self._streams:
            raise RuntimeError("No audio devices configured (need at least mic or system)")

        for s in self._streams:
            s.start()
        return self._async_q

    async def stop(self) -> None:
        for s in self._streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self._streams.clear()
