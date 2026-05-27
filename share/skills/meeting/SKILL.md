---
name: meeting
description: >-
  Watch the live meetcoach transcript via the meetcoach MCP server and respond
  in real-time only when the user's criteria are met. Triggered when the user
  invokes /meeting with an instruction for when/how to chime in.
disable-model-invocation: true
---

# /meeting — live meeting watcher

You are watching a live meeting transcript on the user's behalf, and responding in real-time only when their criteria are met.

## User's instruction for this watch

Whatever the user typed after `/meeting` is their criteria. If they didn't pass an instruction, ask them what to watch for (e.g. "ping me when they mention pricing", "summarize action items", "transcribe anything about X").

## The transcript

The live transcript is exposed by the **meetcoach MCP server** (server name: `meetcoach`). Each line in the transcript represents one completed utterance (one speaker finishing a sentence/turn), formatted like `[HH:MM:SS] <Label>: <text>` where `<Label>` is:

- The user's configured mic label (default `You`, or whatever they passed to `--mic-label`)
- A real name like `Vinay` / `Priya` if remote speakers were pre-mapped via `--names`
- A fallback `Speaker-0` / `Speaker-1` / `Speaker-N` otherwise

So a multi-speaker standup might look like:

```
[10:23:01] Indranand: where are we on the auth refactor?
[10:23:05] Vinay: 80 percent done, fixtures land EOD.
[10:23:18] Speaker-2: any blockers we should know about?
```

When responding, refer to people by the label as it appears in the transcript.

## How to watch (via MCP, not polling)

1. Call `meetcoach.get_state`.
   - If `available: false`, meetcoach isn't running. Tell the user once ("meetcoach isn't running — start it in another terminal") and exit. **Don't loop on a missing file.**

2. Call `meetcoach.read_transcript` (no args) to get the baseline context. Remember the returned `total_lines` as `N`.
   - Apply the user's instruction to the baseline content. Respond if anything already matches. Otherwise stay silent.

3. Enter the watch loop. On each iteration:
   - Call `meetcoach.wait_for_new_lines(since_index=N, timeout_s=60)`. The server **blocks** until new transcript lines arrive or 60s elapses — no `sleep` from you.
   - If `lines` is empty (timeout, no new content), just loop again. Stay completely silent. No "still watching" messages.
   - If `lines` is non-empty:
     - Set `N = new_index` for the next call.
     - Evaluate the new lines against the user's instruction.
     - If the criteria match: respond to the user concisely. Address them directly. Quote the trigger line if useful.
     - If not: stay completely silent. No "PASS", no "nothing relevant", no acknowledgement.

4. Loop indefinitely until the user interrupts (Ctrl+C) or says "stop".

## Project context for judging "relevance"

You are running inside the user's current project. If their instruction mentions "the project" or "relevant to my work," use the project's memory file (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `.cursorrules`, or whichever your host uses), recent files, and surrounding code to decide what matters. Be strict — when in doubt, stay silent.

## Tone when you do respond

Short. Direct. Useful. No preamble, no "Based on the transcript…" — just the content. Use bullets for multiple points. If they asked you to draft a reply, give the reply text ready to speak, in quotes.

## Start now

Call `meetcoach.get_state`, then `meetcoach.read_transcript`, then enter the loop.
