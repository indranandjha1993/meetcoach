# meetcoach

Live meeting transcription on macOS with Claude as a silent coach. Captures any meeting app (Zoom, Google Meet, Microsoft Teams, etc.) via system audio loopback, transcribes in real-time with Deepgram Flux (Hindi/English code-switching works out of the box), and pipes the rolling transcript to a `claude -p` subprocess that watches the conversation.

Works two ways:

1. **Built-in TUI** — a two-pane terminal app with live transcript on the left and Claude coach suggestions on the right.
2. **`/meeting` slash command** — invoke from any project's Claude Code session with a free-form instruction (`"only ping me when they mention pricing"`, `"draft replies when a question is directed at me"`, etc.). Claude watches the transcript and only chimes in when your criteria match.

## Requirements

- macOS (uses BlackHole as a virtual audio driver for system loopback)
- Python 3.13 (`brew install python@3.13` or via `uv`)
- [`uv`](https://docs.astral.sh/uv/) — `brew install uv`
- [Claude Code](https://docs.claude.com/en/docs/claude-code) — the `claude` binary on PATH (uses your Claude Max plan; no Anthropic API key needed)
- A Deepgram API key — free tier at https://deepgram.com gives $200 of credit

## Setup on a fresh Mac

### 1. Clone and install Python deps

```bash
git clone https://github.com/indranandjha1993/meetcoach.git
cd meetcoach
uv venv --python 3.13
uv pip install -e .
```

(SSH users: `git clone git@github.com:indranandjha1993/meetcoach.git`.)

### 2. Install BlackHole

BlackHole is a kernel-level virtual audio driver that lets us capture system audio. It needs admin password during install.

```bash
brew install --cask blackhole-2ch
sudo killall coreaudiod     # makes the driver visible without rebooting
```

### 3. Create a Multi-Output Device

This lets you *hear* meeting audio normally *and* simultaneously route a copy into BlackHole for capture.

1. Open **Audio MIDI Setup** (Spotlight: `audio midi`)
2. Click the **+** at the bottom-left → **Create Multi-Output Device**
3. In the right pane, check **Use** for both your usual output (headphones or speakers) AND **BlackHole 2ch**
4. Set **Primary Device** (top dropdown) to your usual output, NOT BlackHole — BlackHole is virtual and shouldn't drive the clock
5. Tick **Drift Correction** on **BlackHole 2ch** only
6. Double-click the device name in the left list and rename it (e.g. `Meeting Capture`)

**To activate for a meeting:** System Settings → Sound → Output → select your new device. Audio plays through your speakers/headphones normally; BlackHole receives a copy.

> Caveat: if you have non-3.5mm headphones (USB-C, Bluetooth, USB) plugged in and you also include **MacBook Pro Speakers** in the Multi-Output group, you'll hear from both. Include only the output you're actively using.

### 4. Configure your API key

```bash
cp .env.example .env
```

Open `.env` and paste your Deepgram key:

```
DEEPGRAM_API_KEY=your_key_here
```

Optional overrides:

```
CLAUDE_BIN=/full/path/to/claude       # if `claude` isn't on PATH
COACH_MODEL=claude-haiku-4-5          # passed to claude -p --model
```

### 5. Install the `/meeting` slash command

```bash
./scripts/install-slash-commands.sh
```

This symlinks `share/slash-commands/*.md` into `~/.claude/commands/` so edits to the repo version flow through without re-running the installer.

### 6. Verify everything

```bash
.venv/bin/meetcoach doctor
```

Expected output:

```
✓ BlackHole found at device index 0
✓ Default mic at device index 1
✓ claude CLI at /opt/homebrew/bin/claude
✓ DEEPGRAM_API_KEY set — using Deepgram streaming STT
```

## Usage

### Terminal A — start capturing

```bash
.venv/bin/meetcoach start
```

The Textual TUI opens with two panes — live transcript on the left, Claude coach on the right. Hotkeys:

- `a` — ask Claude an on-demand question about the meeting so far
- `m` — mute / unmute the mic mid-session (kills mic-bleed when you're listening only; flip back on when you start speaking)
- `p` — pause / resume the auto coach
- `c` — clear the coach pane
- `q` — quit

The active session's transcript is saved to `transcripts/meeting-<timestamp>.txt` and a stable symlink at `~/.meetcoach/current.txt` is repointed to it on every launch.

### Terminal B — drive Claude from your project

In any project's `claude` session, with meetcoach running in the background:

```
/meeting only draft a reply if a question is directed at me; otherwise just briefly tell me what's being discussed every couple of minutes
```

Claude reads `~/.meetcoach/current.txt`, applies your instruction, polls for new lines every 15s, and responds only when your criteria match. The agent also picks up the project's `CLAUDE.md` and surroundings as context for what counts as "relevant to my work."

**Stop** with `Ctrl+C` or by typing `stop watching`.

More example invocations:

```
/meeting alert me if anyone mentions Q3 roadmap, deadlines, or budget
/meeting draft replies when someone asks me a direct question; pre-write them in quotes
/meeting summarize every 5 minutes; flag action items immediately
/meeting transcribe what's relevant to the auth refactor we're working on; ignore everything else
```

### Listen-only mode (no mic)

For testing with YouTube or any workflow where you don't want your mic captured:

```bash
.venv/bin/meetcoach start --no-mic
```

This avoids the "your mic picks up the meeting playing through your speakers and double-transcribes it" problem.

You can also toggle the mic on/off **mid-session** with the `m` hotkey — useful when you alternate between listening and speaking in the same meeting. (When started with `--no-mic`, no mic stream exists, so `m` is a no-op.)

### Speaker labels

By default the transcript uses `You:` for your mic and `Speaker-0:` / `Speaker-1:` / … for remote participants (Deepgram Nova-3 does diarization on the system-audio channel and assigns a stable ID to each distinct voice).

Personalize with two flags:

```bash
.venv/bin/meetcoach start \
  --mic-label "Indranand" \
  --names "Vinay,Priya,Sam"
```

Result in the transcript:
- Your mic becomes `Indranand:`
- The first remote speaker to talk becomes `Vinay:`, the second `Priya:`, the third `Sam:`
- A fourth remote speaker (no name supplied) falls back to `Speaker-3:`

Mapping is first-seen-wins per session and not persisted, so if speakers join in a different order next time you'll need to adjust the `--names` order.

## CLI reference

```
meetcoach devices       List input audio devices
meetcoach doctor        Sanity-check the environment
meetcoach start         Launch the TUI

Common flags for `start`:
  --mic <name|index>          Override default mic (substring match works)
  --system <name|index>       Override system-audio device (default: BlackHole)
  --no-mic                    Skip mic capture (use system audio only)
  --no-system                 Skip system-audio capture (mic only)
  --mic-label NAME            Label for your mic in the transcript (default: "You")
  --names "N1,N2,..."         Names for remote speakers, first-seen-wins assignment
  --interval <seconds>        Coach tick interval (default: 25)
  --model <name>              Pass --model to claude -p
  --engine deepgram|whisper   Force STT backend (default: auto-detect from env)
```

## How it works

```
  Mic               BlackHole
   │                    │
   ▼                    ▼
  ┌────── sounddevice (callback thread) ──────┐
  │ DualCapture → asyncio.Queue[AudioChunk]   │
  └────────────────────┬──────────────────────┘
                       │  int16 PCM @ 16kHz
                       ▼
        Deepgram Nova-3 streaming (raw websocket per source)
                       │  • mic source: language=multi, no diarize → speaker="you"
                       │  • system source: language=multi + diarize=true
                       │      → per-word speaker IDs split into "speaker-N"
                       ▼
            SpeakerLabeler (maps to mic_label / --names)
                       │
        Transcript log  +  ~/.meetcoach/current.txt
                       │
       ┌───────────────┴───────────────┐
       ▼                               ▼
   TUI (left/right panes)     /meeting slash command
       │                               │
   Coach pane                  Claude in your project
       ▲                               ▲
       └── claude -p subprocess ───────┘
           (rolling transcript + instruction prompt)
```

**Why Nova-3 streaming with raw websockets instead of the SDK?** Deepgram recommends Nova-3 specifically for multi-speaker meeting transcription (better noise/crosstalk robustness than the Flux conversational model), and `diarize=true` gives us per-speaker IDs out of the box. We talk to the v1 listen endpoint directly because the `deepgram-sdk`'s `nova-3 language=multi` path hangs silently on connect — bug in the Python SDK, not the API itself.

**Why a `claude -p` subprocess instead of the Anthropic SDK?** Uses your Claude Max plan, no API key needed, no per-call billing.

## Troubleshooting

### `BlackHole not found` after install
Core Audio hasn't picked up the new driver. Run `sudo killall coreaudiod` (briefly cuts audio for ~1s in any app) or reboot.

### Transcript labels things as `You:` when only system audio is playing
Your mic is picking up the meeting playing through your speakers and the STT is transcribing it twice (once via the mic channel as `You:`, once via the system channel with a `Speaker-N:` label). Either wear headphones, or start with `--no-mic`.

### Two speakers share the same `Speaker-N` ID, or one speaker is split across two IDs
Voice diarization isn't perfect — overlapping speech, similar voices, or someone changing mics mid-call can confuse it. Expect roughly 80-90% accuracy on clean 3-5 speaker audio, lower with heavier crosstalk. There's no fix at the meetcoach level; this is a Deepgram diarization limit. If a session has been particularly bad, killing and restarting `meetcoach start` resets the diarization (new session, fresh speaker IDs).

### YouTube/meeting tab hangs on "loading" when you switch system output
Reload the tab. Browsers cache the audio device at page load; changing system output mid-stream confuses them.

### Multi-Output Device sends audio to all checked outputs
That's the intended behavior — there's no auto-fallback. If you uncheck a device that the system happens to be using, audio goes silent until you re-check it or pick a different output. Include only the outputs you actually want active.

### Coach pane stays empty
The auto coach only fires every 25s and stays silent when nothing is notable. Press `[a]` and ask a direct question to verify the `claude -p` subprocess works. If you get an error, check `which claude` and set `CLAUDE_BIN` in `.env`.

### Shutdown traceback when quitting
Should be fixed as of commit `ac69608`. If you see a `RuntimeError` on `[q]`/Ctrl+C, please report the exact trace.

## Project status

Pre-alpha. Personal tool. No automated tests yet. Treat Claude's suggestions as drafts, not facts — verify before acting on anything that matters.
