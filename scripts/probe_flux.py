"""Probe Deepgram Flux v2 (flux-general-multi) directly via websockets.

Captures 25s of BlackHole audio, streams as base64-text frames, prints every
StartOfTurn/EndOfTurn/transcript event. Mirrors the user-provided shell
example but in Python so it integrates with our pipeline.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import sounddevice as sd
import websockets
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DURATION_S = 25
SR = 16000

URL = (
    "wss://api.deepgram.com/v2/listen"
    "?eot_threshold=0.7"
    "&eot_timeout_ms=5000"
    "&model=flux-general-multi"
    "&encoding=linear16"
    f"&sample_rate={SR}"
)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def main() -> int:
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        log("DEEPGRAM_API_KEY missing"); return 2

    devs = sd.query_devices()
    bh_idx = next(
        (i for i, d in enumerate(devs)
         if "blackhole" in d["name"].lower() and d["max_input_channels"] > 0),
        None,
    )
    if bh_idx is None:
        log("BlackHole not found"); return 2
    log(f"BlackHole at device {bh_idx}")

    log(f"connecting to {URL}")
    headers = {"Authorization": f"Token {key}"}
    counters: dict[str, int] = {}

    try:
        ws = await asyncio.wait_for(
            websockets.connect(URL, additional_headers=headers),
            timeout=10,
        )
    except Exception as e:
        log(f"connect failed: {type(e).__name__}: {e}")
        return 3
    log("connected")

    async def reader() -> None:
        try:
            async for msg in ws:
                try:
                    raw = msg.encode() if isinstance(msg, str) else msg
                    data = json.loads(raw)
                except Exception as e:
                    log(f"decode error: {e} raw={msg[:120]!r}")
                    continue
                evt = data.get("event", "")
                ti = data.get("turn_index", "")
                tr = (data.get("transcript") or "").strip()
                eot = data.get("end_of_turn_confidence")
                counters[evt or "unknown"] = counters.get(evt or "unknown", 0) + 1
                if evt == "StartOfTurn":
                    log(f"--- StartOfTurn turn={ti}")
                if tr:
                    log(f"  [{ti}] {tr}")
                if evt == "EndOfTurn":
                    log(f"--- EndOfTurn turn={ti} conf={eot}")
                if evt not in {"StartOfTurn", "EndOfTurn"} and not tr:
                    log(f"event={evt!r} data_keys={list(data.keys())}")
        except websockets.exceptions.ConnectionClosed as e:
            log(f"reader: connection closed: {e}")

    reader_task = asyncio.create_task(reader())

    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
    loop = asyncio.get_running_loop()

    def cb(indata, _frames, _time, status):  # noqa: ANN001
        if status:
            log(f"audio status: {status}")
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata
        pcm = np.clip(mono * 32767, -32768, 32767).astype(np.int16).tobytes()
        loop.call_soon_threadsafe(q.put_nowait, pcm)

    stream = sd.InputStream(
        device=bh_idx, channels=2, samplerate=SR,
        blocksize=SR // 10, dtype="float32", callback=cb,
    )
    stream.start()
    log(f"streaming BlackHole audio for {DURATION_S}s...")

    deadline = time.time() + DURATION_S
    sent = 0
    while time.time() < deadline:
        try:
            pcm = await asyncio.wait_for(q.get(), timeout=deadline - time.time())
        except TimeoutError:
            break
        try:
            await ws.send(pcm)
            sent += 1
        except Exception as e:
            log(f"send error: {e}"); break

    stream.stop(); stream.close()
    log(f"done streaming: {sent} chunks sent, events={counters}")
    log("waiting 3s for trailing events...")
    await asyncio.sleep(3)
    log(f"final events={counters}")

    await ws.close()
    reader_task.cancel()
    try:
        await reader_task
    except (asyncio.CancelledError, Exception):
        pass
    return 0 if sum(v for k, v in counters.items() if k != "Error") > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
