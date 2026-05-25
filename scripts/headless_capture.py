"""Headless 45s capture from BlackHole + Deepgram, printing finalized lines.

For verifying the pipeline outside the TUI. Run: python scripts/headless_capture.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from meetcoach.audio import DualCapture, find_blackhole
from meetcoach.config import Settings
from meetcoach.stt import DeepgramTranscriber

DURATION_S = 45


async def main() -> int:
    settings = Settings()
    if not settings.deepgram_key:
        print("DEEPGRAM_API_KEY not set", file=sys.stderr)
        return 2

    bh = find_blackhole()
    if bh is None:
        print("BlackHole not found", file=sys.stderr)
        return 2

    capture = DualCapture(mic_device=None, system_device=bh, sample_rate=16000)
    audio_q = await capture.start()
    transcriber = DeepgramTranscriber(settings.deepgram_key, sample_rate=16000)

    print(f"[capture] listening on BlackHole (device {bh}) for {DURATION_S}s — play YouTube now")
    print("[capture] (system audio is muted to your speakers during this; we hear it via BlackHole)")
    start = time.time()
    n_final = 0

    async def consume() -> None:
        nonlocal n_final
        last_partial = ""
        async for ev in transcriber.run(audio_q):
            t = time.strftime("%H:%M:%S", time.localtime(ev.ts))
            if ev.is_final:
                n_final += 1
                print(f"[{t}] FINAL {ev.speaker}: {ev.text}", flush=True)
                last_partial = ""
            elif ev.text != last_partial:
                tail = ev.text[-100:] if len(ev.text) > 100 else ev.text
                print(f"[{t}] ...   {ev.speaker}: {tail}", flush=True)
                last_partial = ev.text

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(asyncio.shield(consumer), timeout=DURATION_S)
    except asyncio.TimeoutError:
        pass
    finally:
        consumer.cancel()
        await transcriber.close()
        await capture.stop()

    elapsed = time.time() - start
    print(f"\n[capture] done after {elapsed:.1f}s — {n_final} final transcript line(s)")
    return 0 if n_final > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
