# meetcoach

A live meeting copilot for macOS. While you're on a Zoom / Meet / Teams call (or watching a podcast, or sitting in a customer demo), meetcoach captures the audio, transcribes it with speaker labels in real-time, and lets Claude watch the conversation and chime in *only* when you tell it to — using your existing project context.

## Who this is for

You want this if you've ever wanted:

- A live transcript of any meeting with **who said what**, persisted to a file
- An AI assistant that **stays quiet** until something specific happens — "ping me if anyone mentions Q3 budget", "draft a reply when a question is directed at me", "summarize action items every 5 minutes"
- An assistant that **already knows your project** — uses your CLAUDE.md, your codebase, your `.claude/` config — so when it drafts a reply about the auth refactor, it's informed
- All of the above using your existing **Claude Max plan** — no Anthropic API key, no per-call billing

You don't want this if:

- You need a polished consumer product (this is a personal CLI tool, pre-alpha)
- You need real-time *instant* responses (~1-3 second floor with MCP, longer without)
- You don't have a Claude Max plan AND don't want to pay Deepgram for STT (~$0.26/hour)

## Two-minute quick start

If you already have a fresh Mac with Homebrew, `uv`, the Claude CLI, and a Deepgram key:

```bash
git clone https://github.com/indranandjha1993/meetcoach.git
cd meetcoach
uv venv --python 3.13 && uv pip install -e .
brew install --cask blackhole-2ch && sudo killall coreaudiod
cp .env.example .env       # then paste your DEEPGRAM_API_KEY into it
./scripts/install-slash-commands.sh
```

Then do the **two manual steps** that can't be scripted:

1. **Audio MIDI Setup → Multi-Output Device** with your headphones/speakers + BlackHole. Set it as your System Settings → Sound output. (Detailed walkthrough in [Setup §3](#3-create-a-multi-output-device) below.)
2. **Edit `~/.claude/settings.json`** to register the meetcoach MCP server. (See [Setup §6](#6-register-the-meetcoach-mcp-server-with-claude-code).)

Verify:

```bash
.venv/bin/meetcoach doctor
```

You're ready. Jump to [Your first meeting](#your-first-meeting).

## Requirements

- macOS (uses BlackHole as a virtual audio driver for system loopback)
- Python 3.13 (`brew install python@3.13` or via `uv`)
- [`uv`](https://docs.astral.sh/uv/) — `brew install uv`
- [Claude Code](https://docs.claude.com/en/docs/claude-code) — the `claude` binary on PATH (uses your Claude Max plan; no Anthropic API key needed)
- A Deepgram API key — free tier at https://deepgram.com gives $200 of credit (~750 hours at Nova-3 streaming pricing)

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

### 6. Register the meetcoach MCP server with Claude Code

The slash command uses an MCP server (`meetcoach-mcp`) to subscribe to the live transcript — server-side blocking calls instead of agent-side `sleep` polling. One quiet tool call per cycle in your Claude Code chat instead of a wall of `Bash(sleep 15...)` blocks.

Register it once in `~/.claude/settings.json` (or any project's `.mcp.json`):

```json
{
  "mcpServers": {
    "meetcoach": {
      "command": "/Users/indranandjha/Developer/personal/meetcoach/.venv/bin/meetcoach-mcp",
      "args": []
    }
  }
}
```

Adjust the absolute path to your clone. Restart `claude` after editing. Verify by running `claude mcp list` — `meetcoach` should appear.

### 7. Verify everything

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

## How the coach actually works

There are **two independent coach paths**, and you can use them at the same time or separately. They're different and serve different purposes — this is the most common point of confusion.

### Path A — the TUI auto-coach (LLM CLI subprocess)

The Textual TUI's right-hand pane. It fires automatically every 25 seconds. Mechanism:

```
TUI tick (every 25s)
  ├── if new transcript lines exist
  └── spawn `<provider CLI>` with coach instructions + rolling transcript
       └── LLM responds with bullets OR literal "PASS"
            └── if not PASS, write to Coach pane
```

**Pick your LLM provider** with `--coach-provider`:

| Provider | CLI | Install | Default model |
|---|---|---|---|
| `claude` *(default)* | `claude` (Claude Code) | https://docs.claude.com/en/docs/claude-code | Your Max plan's default (Sonnet 4.6 / Opus 4.7) |
| `gemini` | `gemini` (Google's CLI) | `npm install -g @google/gemini-cli` | Whatever `gemini` defaults to |
| `codex` | `codex` (OpenAI's CLI) | `npm install -g @openai/codex` | Whatever `codex exec` defaults to |

`meetcoach start --coach-provider gemini --model gemini-2.5-flash` overrides at launch. If the chosen provider's CLI isn't installed, meetcoach fails fast at startup with an install hint. Run `meetcoach doctor` to see which providers are available on your machine.

- **Where it runs**: a subprocess of meetcoach. Each tick is a fresh CLI invocation.
- **Cost**: $0 per call for `claude` (uses your Max plan). Other providers depend on their billing — Gemini CLI uses your Google AI account, Codex uses your OpenAI account.
- **Project context**: gets nothing automatic. It only sees what you pass — by default just the transcript + coach instructions.
- **Use when**: you want passive monitoring with built-in bullet-style coaching, displayed alongside the live transcript in one terminal.

You can also press `[a]` in the TUI to ask the LLM an on-demand question about the meeting so far — same subprocess path, different system prompt.

### Path B — the `/meeting` slash command (Claude Code + MCP)

You run this **inside a `claude` session in any project directory**. It subscribes to the live transcript via the meetcoach MCP server and watches with custom criteria.

```
You: /meeting only draft a reply if a question is directed at me
       │
       ▼
Claude Code agent (in your project)
  ├── mcp__meetcoach__get_state         (is meetcoach running?)
  ├── mcp__meetcoach__read_transcript   (baseline)
  └── mcp__meetcoach__wait_for_new_lines(since_index=N, timeout=60)
      └── (server blocks until new lines arrive)
       └── evaluate against your instruction
           └── respond inline OR stay silent
            └── loop
```

- **Which LLM**: whatever model your `claude` session is using (matches the model shown at the bottom of Claude Code).
- **Where it runs**: in your already-open `claude` session, with full Claude Code agent capabilities (Read, Bash, MCP tools, etc.).
- **Cost**: $0 per call (uses your Max plan).
- **Project context**: **everything Claude Code sees** — `CLAUDE.md`, `.claude/` config, file Reads, project-scoped MCP servers. Use the instruction to leverage it: *"...relevant to the auth refactor we're working on"*.
- **Use when**: you want criteria-driven, project-aware responses *inside the project context you're already working in*, not a generic monitoring pane.

### Which one should I use?

| Situation | Recommended path |
|---|---|
| Listening to a podcast / news / video alone, just want transcript + occasional bullets | Path A (TUI only) |
| In a meeting with no specific project context, just want "tell me what's going on" | Path A (TUI) |
| In a meeting *about* a specific project, want Claude to draft replies using project context | Path B (`/meeting` in that project's `claude` session) |
| Want both passive monitoring AND project-aware responses | Both — they're independent |

## Your first meeting

End-to-end walkthrough of a real standup.

**Before the meeting (60 seconds):**

System Settings → Sound → Output → **Meeting Capture** (the Multi-Output Device you set up in §3).

**Terminal A — launch meetcoach:**

```bash
cd /Users/yourname/Developer/personal/meetcoach
.venv/bin/meetcoach start \
  --mic-label "Indranand" \
  --names "Vinay,Priya,Sam"
```

The TUI opens. Left pane will fill with transcript lines as people speak. Right pane will show coach output (bullets when something's worth flagging, silent otherwise).

**Terminal B — if you want project-aware help:**

```bash
cd /Users/yourname/Developer/personal/myproject
claude
```

Inside `claude`:

```
/meeting draft a reply if anyone asks me about the auth refactor; otherwise just track action items I should follow up on
```

Now you join the actual meeting in your browser / Zoom / Teams app. Both panes update live.

**During the meeting:**

- Speak normally; the TUI shows `Indranand: <your words>` in cyan, others in yellow/green/etc.
- If someone says "Indranand, can you push that PR by EOD?", a few seconds later your Terminal B chat shows Claude's drafted reply in quotes.
- TUI's Coach pane will surface bullets every 25s if anything notable happened (a question directed at you, a factual error, an action item to capture).
- Press `[a]` in the TUI any time to ask Claude something directly: *"what did Vinay say about the migration timeline?"*

**After the meeting:**

- Press `[q]` in the TUI to quit.
- Transcript is at `transcripts/meeting-<timestamp>.txt`.
- The symlink at `~/.meetcoach/current.txt` still points at it, so you can still `@~/.meetcoach/current.txt` from any `claude` session afterward to ask follow-up questions.

## Workflow recipes

Common patterns. Steal whichever fits.

### Solo listening (podcast, YouTube, news)

```bash
.venv/bin/meetcoach start --no-mic
```

`--no-mic` avoids the mic-bleed echo (your mic picking up the speaker audio and double-transcribing it as `You:`). No project context needed; the TUI is enough.

### Small team standup (3-5 people)

```bash
.venv/bin/meetcoach start \
  --mic-label "Indranand" \
  --names "Vinay,Priya,Sam"
```

In your project's `claude`:

```
/meeting flag any commitment I make or any direct question to me; stay silent otherwise
```

### Large all-hands (listen-only, just want notes)

```bash
.venv/bin/meetcoach start --no-mic
```

In any `claude` session:

```
/meeting alert me if anyone mentions Q3 roadmap, budget, hiring, or the migration; summarize action items at the end
```

### 1-on-1 with manager

```bash
.venv/bin/meetcoach start \
  --mic-label "Indranand" \
  --names "Manager"
```

In your project's `claude`:

```
/meeting after I'm asked about my progress, suggest a 2-sentence answer using my recent commits and the CLAUDE.md context
```

### Customer demo / sales call

```bash
.venv/bin/meetcoach start \
  --mic-label "Indranand" \
  --names "Customer"
```

In a sales-notes project's `claude`:

```
/meeting capture customer objections, feature requests, and pricing questions; ignore my product pitch
```

### Post-meeting follow-ups

After quitting meetcoach, the transcript stays at `~/.meetcoach/current.txt`. In any `claude` session:

```
@~/.meetcoach/current.txt summarize the meeting in 5 bullets, list action items by owner
```

Claude reads the file and answers. No live polling, just one-shot reference.

## Usage

### Terminal A — start capturing

```bash
.venv/bin/meetcoach start
```

The Textual TUI opens with two panes — live transcript on the left, Claude coach on the right. Hotkeys:

- `a` — ask Claude an on-demand question about the meeting so far
- `m` — mute / unmute the mic mid-session
- `p` — pause / resume the auto coach
- `c` — clear the coach pane
- `q` — quit

The active session's transcript is saved to `transcripts/meeting-<timestamp>.txt` and a stable symlink at `~/.meetcoach/current.txt` is repointed to it on every launch.

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
  --mic <name|index>             Override default mic (substring match works)
  --system <name|index>          Override system-audio device (default: BlackHole)
  --no-mic                       Skip mic capture (use system audio only)
  --no-system                    Skip system-audio capture (mic only)
  --mic-label NAME               Label for your mic in the transcript (default: "You")
  --names "N1,N2,..."            Names for remote speakers, first-seen-wins assignment
  --interval <seconds>           Coach tick interval (default: 25)
  --coach-provider <name>        LLM CLI to use: claude (default), gemini, codex
  --coach-bin <path>             Override the binary path for the chosen provider
  --model <name>                 Model name passed through to the coach CLI
  --engine deepgram|whisper      Force STT backend (default: auto-detect from env)

Env-var overrides (read from .env):
  DEEPGRAM_API_KEY               Required for Deepgram STT
  COACH_PROVIDER                 claude | gemini | codex
  COACH_BIN                      Absolute path to the coach CLI binary
  COACH_MODEL                    Default model name passed to the coach CLI

meetcoach-mcp           Launch the MCP server (used by Claude Code, not by you directly)
```

## How it works (under the hood)

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
       ┌───────────────┼─────────────────────────────────┐
       ▼               ▼                                 ▼
   TUI panes      claude -p subprocess        meetcoach-mcp (MCP server)
       │          (auto coach in TUI)                    │
   Coach pane                                  /meeting slash command
                                              in Claude in your project
                                              (wait_for_new_lines blocks
                                               server-side; no polling)
```

**Why Nova-3 streaming with raw websockets instead of the SDK?** Deepgram recommends Nova-3 specifically for multi-speaker meeting transcription (better noise/crosstalk robustness than the Flux conversational model), and `diarize=true` gives us per-speaker IDs out of the box. We talk to the v1 listen endpoint directly because the `deepgram-sdk`'s `nova-3 language=multi` path hangs silently on connect — bug in the Python SDK, not the API itself.

**Why a `claude -p` subprocess instead of the Anthropic SDK?** Uses your Claude Max plan, no API key needed, no per-call billing. We accept the ~300ms subprocess overhead for $0 billing.

**Why an MCP server for the slash command instead of `tail -f`?** A bash polling loop in the slash command made the Claude Code chat a wall of `Bash(sleep 15...)` blocks every 15s, and added 0-15s of latency between a new transcript line and the agent seeing it. The MCP server's `wait_for_new_lines` blocks server-side and returns the moment new content arrives — one clean tool call per cycle, sub-second latency.

## Cost

For a typical 1-hour meeting:

| Component | Cost |
|---|---|
| Deepgram Nova-3 streaming + diarize | ~$0.0043/min × 60 min × 2 channels (mic + system) ≈ **$0.52** |
| Claude (TUI coach, 25s ticks) | **$0** (Claude Max plan) |
| Claude (`/meeting` slash command) | **$0** (Claude Max plan) |
| **Total per hour** | **~$0.52** |

If you only use system audio (start with `--no-mic`), that halves to ~$0.26/hour.

Deepgram's free tier gives $200 credit ≈ 750 hours of dual-channel transcription before you need to add a payment method.

## FAQ

**Q: Why does the transcript say I'm speaking when I'm not?**
A: Your mic is picking up the meeting audio playing through your laptop speakers and double-transcribing it as `You:`. Either wear headphones (mic only hears your real voice), launch with `--no-mic`, or press `[m]` in the TUI to mute mid-session.

**Q: Two of my teammates are getting merged into one speaker ID, or one teammate is getting split into two. Can I fix this?**
A: Not really. That's Deepgram's streaming diarization — ~80-90% accurate on clean 3-5 speaker audio, lower on noisy / similar-voice / crosstalk-heavy calls. The only architectural fix is a post-meeting batch re-diarization pass (e.g., pyannote.audio on the saved transcript audio), which is overkill for live use.

**Q: I'm in a meeting and the `/meeting` slash command says "meetcoach isn't running."**
A: You haven't started meetcoach in Terminal A yet, or you killed it. The slash command reads `~/.meetcoach/current.txt`, which only exists while meetcoach is running. Start it, then re-invoke `/meeting`.

**Q: Does the `/meeting` watcher have access to my MCP servers / subagents / skills in the project?**
A: The slash command's `allowed-tools` includes `mcp__meetcoach__*` plus `Bash` and `Read`. Other tools (other MCP servers, subagents from `.claude/agents/`, skills, Edit, Write) are deliberately not allowed in the watch loop. If you want full project-tool access during the watch, edit `share/slash-commands/meeting.md` and remove or extend the `allowed-tools` line, then re-symlink with `./scripts/install-slash-commands.sh`.

**Q: I have multiple projects. Do I need to install meetcoach in each one?**
A: No. meetcoach itself is installed once. The slash command is user-scoped at `~/.claude/commands/meeting.md` (a symlink into this repo) and works in any project's `claude` session. The MCP server is registered once in `~/.claude/settings.json` and is available globally. The only thing that's per-project is whatever's in each project's `CLAUDE.md` / `.claude/` config, which the `/meeting` agent picks up automatically.

**Q: Can I use this with Anthropic API key instead of Claude Max?**
A: Not as-shipped. The TUI auto-coach can use Claude / Gemini / Codex via their CLIs (see `--coach-provider`), but `claude` specifically uses your Max plan, not the API. The `/meeting` slash command always runs in a Claude Code session, which itself uses your Max plan. If you want to bypass Max, the cleanest path is to use `--coach-provider gemini` or `--coach-provider codex` for the TUI auto-coach (which use Google/OpenAI accounts respectively), and accept that `/meeting` still needs Claude Code.

**Q: What if my preferred coach CLI isn't installed?**
A: Run `meetcoach doctor` — it lists which of `claude`, `gemini`, `codex` are detected on your PATH and gives install hints for the missing ones. If you launch with `--coach-provider X` and that CLI isn't installed, meetcoach exits with an error before opening the TUI — no silent failures.

**Q: Can I use this on Linux or Windows?**
A: macOS-only today. BlackHole is macOS-only; on Linux you'd swap in PulseAudio's loopback module, on Windows you'd use VB-Audio Cable. The rest of the pipeline (sounddevice, Deepgram, MCP server, Claude CLI) is cross-platform — porting is plausible but unimplemented.

**Q: How private is this? Where does my audio go?**
A: Audio goes to **Deepgram** for transcription (their cloud — your audio is on their servers during the call). Transcripts go to **Anthropic** when Claude is invoked (via your `claude` CLI). Transcripts also save to disk at `transcripts/meeting-<ts>.txt`. If you need fully-local, switch the engine: `meetcoach start --engine whisper` runs faster-whisper locally (lower accuracy, no diarization, but no cloud calls). Claude calls still go to Anthropic though.

**Q: Will it work for multilingual meetings (Hindi/English code-switching)?**
A: Yes. Nova-3 with `language=multi` (which we use) supports code-switching across 10 languages including Hindi, Spanish, French, German, Russian, Portuguese, Japanese, Italian, Dutch. Tested working on Hindi/English podcast audio.

## Troubleshooting

### Transcript labels things as `You:` when only system audio is playing
Your mic is picking up the meeting playing through your speakers and the STT is transcribing it twice (once via the mic channel as `You:`, once via the system channel with a `Speaker-N:` label). Either wear headphones, start with `--no-mic`, or press `[m]` in the TUI to mute mid-session.

### Two speakers share the same `Speaker-N` ID, or one speaker is split across two IDs
Voice diarization isn't perfect — overlapping speech, similar voices, or someone changing mics mid-call can confuse it. Expect roughly 80-90% accuracy on clean 3-5 speaker audio, lower with heavier crosstalk. There's no fix at the meetcoach level; this is a Deepgram diarization limit. If a session has been particularly bad, killing and restarting `meetcoach start` resets the diarization (new session, fresh speaker IDs).

### `BlackHole not found` after install
Core Audio hasn't picked up the new driver. Run `sudo killall coreaudiod` (briefly cuts audio for ~1s in any app) or reboot.

### YouTube/meeting tab hangs on "loading" when you switch system output
Reload the tab. Browsers cache the audio device at page load; changing system output mid-stream confuses them.

### Multi-Output Device sends audio to all checked outputs
That's the intended behavior — there's no auto-fallback. If you uncheck a device that the system happens to be using, audio goes silent until you re-check it or pick a different output. Include only the outputs you actually want active.

### Coach pane stays empty
The auto coach only fires every 25s and stays silent when nothing is notable. Press `[a]` and ask a direct question to verify the `claude -p` subprocess works. If you get an error, check `which claude` and set `CLAUDE_BIN` in `.env`.

### `/meeting` slash command falls back to Bash polling instead of MCP tools
Claude Code didn't pick up the MCP server. Check:
1. `~/.claude/settings.json` has the `mcpServers.meetcoach` block with the correct absolute path
2. You restarted `claude` after editing settings
3. Run `claude mcp list` — `meetcoach` should appear with three tools (`get_state`, `read_transcript`, `wait_for_new_lines`)

### Shutdown traceback when quitting the TUI
Should be fixed as of commit `ac69608`. If you see a `RuntimeError` on `[q]` / Ctrl+C, please report the exact trace.

## Glossary

- **BlackHole** — virtual audio driver that lets one app's output be another app's input. We use it to capture macOS system audio.
- **Multi-Output Device** — macOS-built feature that mirrors audio to multiple outputs simultaneously. We use it to send sound to both your headphones AND BlackHole.
- **Diarization** — assigning each piece of speech to a speaker ID. Nova-3 does this on the system-audio channel.
- **EoT (End of Turn / End of Utterance)** — Deepgram's detection that a speaker has finished a sentence. Used to commit a transcript line.
- **Mic source vs system source** — the two audio channels meetcoach captures. Mic = your voice (one speaker). System = everyone else, via BlackHole (multiple speakers, diarized).
- **TUI** — terminal user interface (the Textual app that `meetcoach start` opens).
- **MCP** — Model Context Protocol. The standard for connecting external tools to Claude Code. Our MCP server (`meetcoach-mcp`) lets the `/meeting` slash command subscribe to transcript updates without polling.

## Project status

Pre-alpha. Personal tool. No automated tests yet. Treat Claude's suggestions as drafts, not facts — verify before acting on anything that matters.
