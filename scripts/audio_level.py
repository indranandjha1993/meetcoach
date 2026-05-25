"""5-second BlackHole RMS meter to verify audio is actually flowing in."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import sounddevice as sd

from meetcoach.audio import find_blackhole


def main() -> int:
    bh = find_blackhole()
    if bh is None:
        print("BlackHole not found", file=sys.stderr)
        return 2

    info = sd.query_devices(bh)
    native_sr = int(info["default_samplerate"])
    print(f"BlackHole index={bh}, name={info['name']!r}, native_sr={native_sr}, ch={info['max_input_channels']}")

    for sr in (native_sr, 16000):
        print(f"\n--- testing at {sr} Hz for 5s ---")
        levels = []
        try:
            with sd.InputStream(
                device=bh,
                channels=min(2, info["max_input_channels"]),
                samplerate=sr,
                dtype="float32",
                blocksize=sr // 10,
                callback=lambda indata, *_: levels.append(float(np.sqrt(np.mean(indata**2)))),
            ):
                t0 = time.time()
                while time.time() - t0 < 5:
                    sd.sleep(200)
        except Exception as e:
            print(f"  stream error: {e}")
            continue
        if not levels:
            print("  no callbacks fired")
            continue
        peak = max(levels)
        avg = sum(levels) / len(levels)
        nonzero = sum(1 for l in levels if l > 0.001)
        print(f"  {len(levels)} blocks, avg RMS={avg:.5f}, peak RMS={peak:.5f}, nonzero_blocks={nonzero}")
        if peak < 0.001:
            print("  → SILENT: BlackHole is not receiving audio")
        elif peak < 0.01:
            print("  → very quiet — system audio is routed but at low volume")
        else:
            print("  → AUDIO PRESENT — routing OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
