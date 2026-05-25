"""Diagnostic: confirm our DualCapture pipeline produces non-zero audio AND
that Deepgram is responding (any event, not just transcripts).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from meetcoach.audio import DualCapture, find_blackhole

DURATION_S = 12


async def check_dualcapture_levels() -> None:
    bh = find_blackhole()
    print(f"\n=== Phase 1: DualCapture levels (channels=1, sr=16000) for {DURATION_S}s ===")
    cap = DualCapture(mic_device=None, system_device=bh, sample_rate=16000)
    q = await cap.start()
    n, peak, total = 0, 0.0, 0.0
    deadline = time.time() + DURATION_S
    try:
        while time.time() < deadline:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            arr = chunk.pcm.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(arr**2)))
            peak = max(peak, rms)
            total += rms
            n += 1
    finally:
        await cap.stop()
    avg = total / max(1, n)
    print(f"  chunks={n}, avg_rms={avg:.5f}, peak_rms={peak:.5f}")
    if peak < 0.001:
        print("  → DualCapture is producing SILENT audio (channel mismatch?)")
    else:
        print("  → DualCapture audio looks OK")


async def check_deepgram_events() -> None:
    from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        print("DEEPGRAM_API_KEY missing"); return
    print(f"\n=== Phase 2: Deepgram event probe for {DURATION_S}s ===")
    dg = DeepgramClient(key)
    conn = dg.listen.asyncwebsocket.v("1")
    loop = asyncio.get_running_loop()
    counters: dict[str, int] = {}

    def bump(name: str) -> None:
        counters[name] = counters.get(name, 0) + 1

    def on_open(*a, **k): print("  [dg] OPEN"); bump("open")  # noqa: ANN001,E702
    def on_close(*a, **k): print("  [dg] CLOSE"); bump("close")  # noqa: ANN001,E702
    def on_error(_s, error, **_k): print(f"  [dg] ERROR: {error}"); bump("error")  # noqa: ANN001
    def on_metadata(_s, metadata, **_k): print(f"  [dg] METADATA: {metadata}"); bump("metadata")  # noqa: ANN001
    def on_transcript(_s, result, **_k):  # noqa: ANN001
        bump("transcript")
        try:
            alt = result.channel.alternatives[0]
            txt = (alt.transcript or "").strip()
            print(f"  [dg] T{'(F)' if getattr(result, 'is_final', False) else '(p)'}: {txt!r}")
        except Exception as e:
            print(f"  [dg] T parse error: {e}")

    conn.on(LiveTranscriptionEvents.Open, on_open)
    conn.on(LiveTranscriptionEvents.Close, on_close)
    conn.on(LiveTranscriptionEvents.Error, on_error)
    conn.on(LiveTranscriptionEvents.Metadata, on_metadata)
    conn.on(LiveTranscriptionEvents.Transcript, on_transcript)

    opts = LiveOptions(
        model="nova-3",
        language="multi",
        encoding="linear16",
        sample_rate=16000,
        channels=1,
        smart_format=True,
        interim_results=True,
    )
    try:
        ok = await conn.start(opts)
        print(f"  [dg] start() returned {ok}")
    except Exception as e:
        print(f"  [dg] start() raised: {e}")
        return

    bh = find_blackhole()
    cap = DualCapture(mic_device=None, system_device=bh, sample_rate=16000)
    q = await cap.start()
    deadline = time.time() + DURATION_S
    n_sent = 0
    try:
        while time.time() < deadline:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            try:
                await conn.send(chunk.pcm.tobytes())
                n_sent += 1
            except Exception as e:
                print(f"  [dg] send error: {e}")
                break
    finally:
        await cap.stop()
        try:
            await conn.finish()
        except Exception as e:
            print(f"  [dg] finish error: {e}")
    print(f"  bytes_chunks_sent={n_sent}, event_counts={counters}")


async def main() -> int:
    await check_dualcapture_levels()
    await check_deepgram_events()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
