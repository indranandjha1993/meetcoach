from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent.parent
TRANSCRIPT_DIR = ROOT / "transcripts"


@dataclass(slots=True)
class Settings:
    mic_device: str | int | None = None
    system_device: str | int | None = None
    sample_rate: int = 16000
    engine: str = "auto"  # auto | deepgram | whisper
    whisper_model: str = "small.en"
    coach_interval: float = 25.0
    coach_min_new_chars: int = 80
    coach_model: str | None = field(default_factory=lambda: os.getenv("COACH_MODEL"))
    claude_bin: str = field(default_factory=lambda: os.getenv("CLAUDE_BIN", "claude"))
    deepgram_key: str | None = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY"))
    transcript_dir: Path = TRANSCRIPT_DIR

    def resolve_engine(self) -> str:
        if self.engine != "auto":
            return self.engine
        return "deepgram" if self.deepgram_key else "whisper"
