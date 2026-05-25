"""Minimal Deepgram probe. Chatty, no DualCapture, no shutdown surprises.

Sends 10s of synthesized PCM (sine wave + white noise) to confirm Deepgram
responds at all. Then sends 10s of real BlackHole audio. Each step has a
hard timeout so we always see where it hangs.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import sounddevice as sd


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def probe() -> None:
    from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        log("no DEEPGRAM_API_KEY"); return
    log(f"key set, len={len(key)}")

    log("constructing DeepgramClient...")
    dg = DeepgramClient(key)
    log("getting asyncwebsocket v1 handle...")
    conn = dg.listen.asyncwebsocket.v("1")

    counters: dict[str, int] = {}
    def bump(name: str) -> None: counters[name] = counters.get(name, 0) + 1

    def on_open(*a, **k): log("DG OPEN"); bump("open")  # noqa: ANN001,E702
    def on_close(*a, **k): log("DG CLOSE"); bump("close")  # noqa: ANN001,E702
    def on_error(_s, error, **_k): log(f"DG ERROR: {error}"); bump("error")  # noqa: ANN001
    def on_metadata(_s, metadata, **_k): log(f"DG METADATA: {str(metadata)[:200]}"); bump("metadata")  # noqa: ANN001
    def on_unhandled(*a, **k): log(f"DG UNHANDLED: a={a} k={k}"); bump("unhandled")  # noqa: ANN001
    def on_transcript(_s, result, **_k):  # noqa: ANN001
        bump("transcript")
        try:
            alt = result.channel.alternatives[0]
            txt = (alt.transcript or "").strip()
            kind = "F" if getattr(result, "is_final", False) else "p"
            log(f"DG T({kind}): {txt!r}")
        except Exception as e:
            log(f"DG T parse error: {e}")

    conn.on(LiveTranscriptionEvents.Open, on_open)
    conn.on(LiveTranscriptionEvents.Close, on_close)
    conn.on(LiveTranscriptionEvents.Error, on_error)
    conn.on(LiveTranscriptionEvents.Metadata, on_metadata)
    conn.on(LiveTranscriptionEvents.Transcript, on_transcript)
    conn.on(LiveTranscriptionEvents.Unhandled, on_unhandled)

    opts = LiveOptions(
        model="nova-3",
        language="multi",
        encoding="linear16",
        sample_rate=16000,
        channels=1,
        smart_format=True,
        interim_results=True,
        endpointing=300,
    )
    log(f"opts={opts}")

    log("calling conn.start(opts) with 15s timeout...")
    try:
        ok = await asyncio.wait_for(conn.start(opts), timeout=15)
        log(f"start() returned: {ok}")
    except TimeoutError:
        log("start() TIMED OUT — Deepgram never opened the websocket")
        return
    except Exception as e:
        log(f"start() raised: {type(e).__name__}: {e}")
        return

    log("PHASE A: sending 8s of synthesized speech-band noise...")
    sample_rate = 16000
    t0 = time.time()
    sent_a = 0
    rng = np.random.default_rng(0)
    while time.time() - t0 < 8:
        # 100ms blocks of band-limited noise (more speech-like than pure tone)
        block = (rng.normal(0, 0.2, size=sample_rate // 10) * 32767).astype(np.int16)
        try:
            await conn.send(block.tobytes())
            sent_a += 1
        except Exception as e:
            log(f"send error: {e}"); break
        await asyncio.sleep(0.1)
    log(f"PHASE A sent={sent_a} blocks, counters so far={counters}")
    log("waiting 2s for Deepgram to flush...")
    await asyncio.sleep(2)
    log(f"after flush: counters={counters}")

    log("PHASE B: sending 12s of real BlackHole audio...")
    devs = sd.query_devices()
    bh_idx = next(
        (i for i, d in enumerate(devs)
         if "blackhole" in d["name"].lower() and d["max_input_channels"] > 0),
        None,
    )
    if bh_idx is None:
        log("BlackHole not found"); return

    q: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()

    def cb(indata, frames, time_info, status):  # noqa: ANN001
        if status: log(f"audio status: {status}")
        # indata is float32 (channels, frames); BlackHole returns (frames, 2)
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata
        pcm = np.clip(mono * 32767, -32768, 32767).astype(np.int16)
        loop.call_soon_threadsafe(q.put_nowait, pcm.copy())

    stream = sd.InputStream(
        device=bh_idx, channels=2, samplerate=sample_rate,
        blocksize=sample_rate // 10, dtype="float32", callback=cb,
    )
    stream.start()
    t0 = time.time()
    sent_b = 0
    while time.time() - t0 < 12:
        try:
            pcm = await asyncio.wait_for(q.get(), timeout=1)
        except TimeoutError:
            log("audio queue empty for 1s")
            continue
        try:
            await conn.send(pcm.tobytes())
            sent_b += 1
        except Exception as e:
            log(f"send error: {e}"); break
    stream.stop(); stream.close()
    log(f"PHASE B sent={sent_b} blocks, counters={counters}")
    log("waiting 3s for final Deepgram results...")
    await asyncio.sleep(3)
    log(f"final counters={counters}")

    log("calling conn.finish() with 5s timeout...")
    try:
        await asyncio.wait_for(conn.finish(), timeout=5)
        log("finish() done")
    except TimeoutError:
        log("finish() timed out (we don't care, exiting)")


if __name__ == "__main__":
    asyncio.run(probe())
