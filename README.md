# meetcoach

A personal **meeting copilot** for macOS. Runs in your terminal during any Zoom / Google Meet / Teams call (or podcast, or sales call). Captures the audio, transcribes with speaker labels, and lets Claude / Gemini / Codex watch the conversation and chime in only when *you* tell it to — using your project's context.

Two ways to interact:
- **TUI** — a two-pane terminal app: live transcript on the left, a coach (Claude `-p` subprocess by default) writing short notes every 25s on the right.
- **`/meeting` handler** — invoke from any LLM tool's chat (Claude Code, Cursor, Gemini CLI, Codex CLI). Type a free-form instruction like `"draft a reply when I'm directly asked"`, and the LLM watches the live transcript through our MCP server and only responds when your criteria match.

Status: pre-alpha, personal tool. Treat coach output as drafts, not facts.

---

## Who this is for

- You want a live, speaker-labeled transcript of every meeting, persisted to a file.
- You want an AI assistant that **stays quiet** until something specific happens — "ping me if anyone mentions Q3 budget", "summarize action items at the end", "draft replies for questions directed at me".
- You want the assistant to **already know your project** — uses your `CLAUDE.md` / `AGENTS.md` / repo state.
- You want all of this with your existing **Claude Max plan / Gemini account / OpenAI account** — no extra API key, no per-call billing for the LLM (Deepgram is paid; ~$0.26/hour for STT).

Skip this if you need a polished consumer product, real-time *instant* responses (~1-3s floor), or non-macOS support (BlackHole is macOS-only; Linux audio capture is unimplemented).

---

## Quick start

If you already have Homebrew, `uv`, an LLM CLI (Claude / Gemini / Codex), and a Deepgram key:

```bash
git clone https://github.com/indranandjha1993/meetcoach.git
cd meetcoach
make setup                    # installs deps + BlackHole + slash handlers + MCP registration
cp .env.example .env          # paste DEEPGRAM_API_KEY=<your_key> into it
```

Two manual steps the installer can't do:

1. **Audio MIDI Setup** → create a Multi-Output Device with your headphones/speakers + BlackHole. Set it as System Settings → Sound → Output. (Detail in [§ Full setup](#full-setup-fresh-mac).)
2. Optional: `make register-mcp` if `make setup` didn't auto-detect your LLM tools.

Verify and run:

```bash
make doctor                   # green dots = ready
make start                    # opens the TUI (or `make listen` to skip mic capture)
```

> **Linux / WSL:** `make install`, `make slash-commands`, `make register-mcp`, and the `/meeting` handler all work. Live audio capture doesn't yet — BlackHole is macOS-only and the PulseAudio/PipeWire backend isn't implemented.

---

## Full setup (fresh Mac)

`make setup` chains the first three steps. The rest are manual or one-time configuration. If you just ran `make setup`, jump to step 4.

### 1. Clone and install Python deps

```bash
git clone https://github.com/indranandjha1993/meetcoach.git
cd meetcoach
make install                  # uv venv --python 3.13 && uv pip install -e .
```

Requires: macOS, Python 3.13, [`uv`](https://docs.astral.sh/uv/) (`brew install uv`), and at least one of: Claude Code, Gemini CLI, Codex CLI.

### 2. Install BlackHole

Kernel-level virtual audio driver. Install needs your admin password.

```bash
make audio-setup              # brew install --cask blackhole-2ch && sudo killall coreaudiod
```

### 3. Create a Multi-Output Device (one-time, GUI)

So you can both *hear* meeting audio *and* mirror it into BlackHole for capture.

1. Open **Audio MIDI Setup** (Spotlight: `audio midi`).
2. Click the **+** at the bottom-left → **Create Multi-Output Device**.
3. Check **Use** for both **BlackHole 2ch** and your real output (headphones/speakers).
4. Set **Primary Device** to your real output (not BlackHole — it's virtual).
5. Tick **Drift Correction** on **BlackHole 2ch** only.
6. Rename it (e.g. `Meeting Capture`) by double-clicking the device name.

**Activate before each meeting:** System Settings → Sound → Output → select your Multi-Output Device.

> If you have USB / Bluetooth headphones AND `MacBook Pro Speakers` both in the group, you'll hear from both. Include only the output you're using.

### 4. Configure the Deepgram API key

```bash
cp .env.example .env
# then edit .env, paste:  DEEPGRAM_API_KEY=your_key_here
```

Get a free key at https://deepgram.com — $200 credit, ~750 hours of dual-channel transcription.

Optional env overrides:
- `COACH_PROVIDER=claude|gemini|codex` — default coach LLM
- `COACH_MODEL=claude-haiku-4-5` — model passed to the coach CLI
- `COACH_BIN=/full/path` — override binary path

### 5. Install the `/meeting` handler in your LLM tool(s)

```bash
make slash-commands
```

Auto-detects Claude Code, Cursor, Gemini CLI, Codex CLI — installs `/meeting` (or its skill-format equivalent) into each tool's **on-demand command/skill directory**, never into a memory file (`CLAUDE.md`, `GEMINI.md`, etc.). Your project memory stays clean.

### 6. Register the MCP server with your LLM tool(s)

```bash
make register-mcp
```

Auto-detects each installed tool and registers the `meetcoach-mcp` server using that tool's native mechanism:

| Tool | Mechanism | Stored at |
|---|---|---|
| Claude Code | `claude mcp add -s user ...` | `~/.claude.json` |
| Gemini CLI | `gemini mcp add -s user ...` | `~/.gemini/settings.json` |
| Codex CLI | `codex mcp add ... -- <bin>` | `~/.codex/config.toml` |
| Cursor | merge JSON (no CLI) | `~/.cursor/mcp.json` |

Idempotent — re-runs are no-ops.

### 7. Verify

```bash
make doctor
```

Every group should be green. Run `meetcoach doctor --verbose` to get copy-paste fix instructions for any item that isn't.

---

## Using it

### The TUI

```bash
make start                    # mic + system audio
make listen                   # system audio only (good for solo testing)
```

Top bar: live indicators for Audio / STT / Coach / MCP. Right-hand pane is the auto-coach (default: `claude -p` every 25s, returns short notes or "PASS").

**Hotkeys:**

- `a` — ask the coach a question now (opens a centered modal)
- `m` — mute / unmute the mic mid-session
- `s` — cycle coach provider through installed CLIs
- `r` — reconnect Deepgram (drop + reopen the STT websockets)
- `p` — pause / resume the auto coach
- `c` — clear the coach pane
- `?` — full status detail (every capability + fix steps)
- `q` — quit

For everything else (reinstall slash handlers, copy MCP config, open transcripts folder, show the `/meeting` prompt body), press the **Textual command palette** (`Ctrl+\` by default). Type to filter.

### `/meeting` in other LLM tools

**The invocation mechanism differs per tool.** Slash commands aren't universal.

| Tool | How you invoke it |
|---|---|
| **Claude Code** | `/meeting <instruction>` — direct slash command ✓ |
| **Cursor** | `/meeting <instruction>` — direct skill invocation ✓ |
| **Gemini CLI** | Natural-language prompt (skill auto-invokes from description match) |
| **Codex CLI** | Natural-language prompt (same — Codex reserves `/` for built-in commands) |

In Claude Code / Cursor:

```
/meeting only draft a reply if a question is directed at me; otherwise just briefly tell me what's being discussed
/meeting alert me if anyone mentions Q3 roadmap, deadlines, or budget
/meeting transcribe what's relevant to the auth refactor we're working on; ignore everything else
```

In Gemini CLI / Codex CLI, **describe what you want — the skill will auto-invoke**:

```
> watch the live meeting and tell me what's being discussed
> follow this call and alert me if I'm directly asked a question
> use the meeting skill to summarize action items as they come up
> monitor the podcast transcript and flag anything about pricing
```

Stop the watcher with `Ctrl+C` or by saying "stop watching".

The MCP server (`meetcoach-mcp`) is the same for every tool — it exposes `get_state`, `read_transcript`, `wait_for_new_lines` so the LLM doesn't have to poll. One subscription call per turn instead of `sleep 15` + `tail` every 15s.

### Workflow recipes

| Use case | Command |
|---|---|
| Solo podcast / YouTube (avoid mic-bleed) | `make listen` |
| Standup with named teammates | `meetcoach start --mic-label "Indranand" --names "Vinay,Priya,Sam"` |
| All-hands, just want notes | `make listen` + `/meeting capture action items, summarize at end` |
| 1-on-1 with manager | `meetcoach start --names "Manager"` + `/meeting after I'm asked about progress, suggest a 2-sentence answer using CLAUDE.md context` |
| Customer call / demo | `meetcoach start --names "Customer"` + `/meeting capture objections, pricing questions, feature requests` |
| Post-meeting follow-up | Quit meetcoach, then in any `claude` session: `@~/.meetcoach/current.txt summarize this meeting in 5 bullets, list owners` |

### Speaker labels

By default the transcript uses `You:` for your mic and `Speaker-0:` / `Speaker-1:` / … for remote participants. Personalize:

```bash
meetcoach start \
  --mic-label "Indranand" \
  --names "Vinay,Priya,Sam"
```

Names are assigned first-seen-first, not persisted. Re-launch with different `--names` order if join sequence changes.

---

## How it works

```
   Mic                       BlackHole
    │                            │
    ▼                            ▼
   ┌──── sounddevice (callback thread) ────┐
   │  DualCapture → asyncio.Queue          │
   └────────────────────┬──────────────────┘
                        │  int16 PCM @ 16kHz
                        ▼
        Deepgram Nova-3 streaming (raw websocket per source)
            mic source:    language=multi, no diarize → "you"
            system source: language=multi, diarize=true → "speaker-N"
                        │
                        ▼
            SpeakerLabeler  (maps to --mic-label / --names)
                        │
        ┌───────────────┴───────────────┐
        ▼                               ▼
   transcripts/meeting-<ts>.txt   ~/.meetcoach/current.txt  (stable symlink)
        │                               │
   ┌────┴──────────┐         ┌──────────┴──────────┐
   ▼               ▼         ▼                     ▼
  TUI panes   coach.py    meetcoach-mcp     /meeting handler
                │         (MCP server)      in any LLM tool
            claude/gemini/codex -p          (subscribes to current.txt
            subprocess every 25s            via MCP, responds per
            (also: [a] on-demand)           user's instruction)
```

**Why Deepgram Nova-3 over Flux?** Nova-3 is Deepgram's recommended model for multi-speaker meetings (better noise / crosstalk robustness; Flux is built for voice agents). `diarize=true` gives us per-speaker IDs out of the box. We talk to the v1 listen endpoint directly because `deepgram-sdk`'s `nova-3 language=multi` path hangs on connect — bug in the Python SDK, not the API.

**Why CLI subprocess for the auto-coach instead of the Anthropic SDK?** Uses your Claude Max plan (or Gemini / OpenAI account), no API key, no per-call billing. ~300ms subprocess spawn overhead per 25s tick — negligible.

**Why MCP for `/meeting` instead of `tail -f`?** The bash polling pattern made the LLM tool's chat noisy (visible `Bash(sleep 15...)` every 15s) and added 0-15s latency between a new transcript line and the agent seeing it. `wait_for_new_lines` blocks server-side and returns the moment new content arrives — one quiet tool call per cycle, sub-second latency.

---

## Reference

### Make targets

Run `make` (no args) for the live list.

```
make setup            First-time bootstrap (install + audio + slash commands + MCP)
make install          Create the .venv and install deps
make audio-setup      Install BlackHole + restart Core Audio (macOS only)
make install-fonts    Install Noto fonts for proper Devanagari/Hindi rendering
make slash-commands   Install /meeting handler into every detected LLM tool
make register-mcp     Register meetcoach-mcp with every detected LLM tool
make doctor           Sanity-check the environment
make start            Launch the live TUI
make listen           Launch the TUI in listen-only mode (no mic)
make mcp              Run the MCP server in foreground (debug)
make prompt           Print the /meeting prompt body to stdout
make prompt-copy      Copy the /meeting prompt to clipboard
make lint             Run ruff linter
make format           Auto-format with ruff
make clean            Remove venv and caches (keeps .env and transcripts/)
make update           git pull + reinstall
```

### CLI flags (for `meetcoach start`)

```
--mic <name|index>          Override default mic (substring match works)
--system <name|index>       Override system-audio device (default: BlackHole)
--no-mic                    Skip mic capture
--no-system                 Skip system-audio capture
--mic-label NAME            Label for your mic in the transcript (default: "You")
--names "A,B,C"             Names for remote speakers, first-seen-wins assignment
--interval <seconds>        Coach tick interval (default: 25)
--coach-provider <name>     LLM CLI to use: claude (default), gemini, codex
--coach-bin <path>          Override the binary path for the chosen provider
--model <name>              Model passed to the coach CLI
--engine deepgram|whisper   Force STT backend (default: auto from env)
```

### Cost (rough)

| Component | Per hour |
|---|---|
| Deepgram Nova-3 streaming + diarize (system audio) | ~$0.26 |
| Mic channel (if `--no-mic` not set) | ~$0.26 |
| Claude / Gemini / Codex coach | $0 (uses your existing subscription) |
| **Total typical hour** | **~$0.26 (listen-only) to ~$0.52 (with mic)** |

### Troubleshooting

**Transcript labels things as `You:` when only system audio is playing.** Your mic is picking up the meeting from your speakers. Wear headphones, launch with `--no-mic`, or press `[m]` in the TUI to mute mid-session.

**Two teammates merge into one speaker, or one teammate splits into two.** Deepgram's streaming diarization is ~80-90% accurate on clean 3-5 speaker audio, lower on noisy / similar-voice / crosstalk-heavy calls. Not a meetcoach bug; restart the session to reset the speaker mapping.

**`BlackHole not found` after install.** Core Audio hasn't picked up the driver. `sudo killall coreaudiod` or reboot.

**YouTube/meeting tab hangs "loading" when you switch system output.** Reload the tab. Browsers cache audio device at page load.

**`/meeting` returns "Unrecognized command" in Codex/Gemini.** Those tools don't support user-invoked slash commands for custom skills — they auto-invoke from natural-language prompts. See [§ Using it](#meeting-in-other-llm-tools). Use a phrase like `"watch the meeting and ..."` instead.

**Coach pane stays empty.** Auto coach only fires every 25s and stays silent when nothing is notable. Press `[a]` and ask directly to verify the LLM CLI is wired. If it errors, run `meetcoach doctor` and check the Coach group.

**Capability bar shows red MCP.** Run `make register-mcp` — registers with every installed tool and re-runs are no-ops. Check `make doctor` afterward.

**Hindi / Devanagari (or other non-Latin) text renders with weird gaps in the transcript pane.** The default monospace fonts on most terminals (SF Mono, Menlo, Monaco) don't have Devanagari glyphs — the terminal falls back to a different font whose metrics don't match, producing wide-spaced output. Install Noto fonts:

```bash
make install-fonts            # brew installs font-noto-sans-mono + font-noto-sans-devanagari
```

Then **manually configure your terminal** to use Noto Sans Mono (`make install-fonts` prints exact steps for Terminal.app / iTerm2 / Ghostty / Kitty). meetcoach can't change your terminal's font setting — that's a one-time per-terminal config. Other terminals worth considering for better international text rendering: **Ghostty**, **Kitty**, **WezTerm**.

### FAQ

**Can I use this with an API key instead of a CLI subscription?**
Not as-shipped. The auto-coach goes through `claude -p` / `gemini` / `codex exec` subprocesses, all of which use their respective vendor subscriptions. Switching to direct SDK calls would mean refactoring `src/meetcoach/providers.py`.

**Does the `/meeting` watcher have access to my project's MCP servers / skills?**
The slash command's `allowed-tools` is restricted to `mcp__meetcoach__*` plus `Bash` and `Read` for safety during the watch loop. To give it full project-tool access, edit `share/slash-commands/meeting.md`, remove the `allowed-tools` line, then `make slash-commands` to re-symlink.

**Multiple projects — install once?**
Yes. meetcoach itself is installed once. The slash handler is user-scoped (symlinks in `~/.claude/commands/`, `~/.cursor/skills-cursor/`, etc.) and the MCP server is registered at user scope. Per-project, you only need a `CLAUDE.md` / `AGENTS.md` if you want context-aware coaching.

**Privacy — where does my audio go?**
Audio goes to **Deepgram** (their cloud). Transcripts go to **Anthropic / Google / OpenAI** depending on coach provider. Transcripts also save to local disk at `transcripts/meeting-<ts>.txt`. For fully-local STT, `meetcoach start --engine whisper` runs `faster-whisper` on your machine (lower accuracy, no diarization, no cloud STT calls — coach LLM calls still go to the vendor).

**Multilingual / Hindi-English code-switching?**
Yes. Nova-3 with `language=multi` supports 10 languages including Hindi, Spanish, French, German, Russian, Portuguese, Japanese, Italian, Dutch. Tested working on Hindi/English podcast audio.

---

## Project status

Pre-alpha. Personal tool. No automated tests. Treat coach suggestions as drafts — verify before acting on anything that matters. Tonight's commits and the `make` target list show the current state; the [`/meeting` skill prompt](share/skills/meeting/SKILL.md) is the source of truth for how the LLM is instructed to watch.
