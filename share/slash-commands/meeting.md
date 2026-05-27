---
description: Watch the live meetcoach transcript and respond per the user's instruction
allowed-tools: Bash, Read
argument-hint: "<your instruction for when/how to respond>"
---

You are watching a live meeting transcript on the user's behalf, and responding in real-time only when their criteria are met.

## User's instruction for this watch

$ARGUMENTS

## The transcript

The live transcript is at `~/.meetcoach/current.txt`. It's updated by `meetcoach` (running in another terminal) as the meeting progresses — new lines are appended as each speaker finishes an utterance. Each line is formatted like `[HH:MM:SS] <Label>: <text>` where `<Label>` is one of:

- The user's configured mic label (default `You`, or whatever they passed to `--mic-label`)
- A real name like `Vinay` / `Priya` if they pre-mapped remote speakers via `--names`
- A fallback `Speaker-0` / `Speaker-1` / `Speaker-N` when no name was supplied for that speaker

So a multi-speaker standup might look like:

```
[10:23:01] Indranand: where are we on the auth refactor?
[10:23:05] Vinay: 80 percent done, fixtures land EOD.
[10:23:18] Speaker-2: any blockers we should know about?
```

When responding, refer to people by the label as it appears in the transcript.

If the file doesn't exist or is empty, meetcoach isn't running yet. Tell the user that once, then exit — don't loop on an empty file.

## How to watch

1. Read the transcript so far with `cat ~/.meetcoach/current.txt`. This is your baseline context — apply the user's instruction to it now and respond if anything already matches.

2. Note the current line count: `wc -l < ~/.meetcoach/current.txt | tr -d ' '`. Remember this as `N`.

3. Enter a watch loop. On each iteration:
   - `sleep 15`
   - `tail -n +$((N+1)) ~/.meetcoach/current.txt` — this fetches any lines added since the last check.
   - If empty, continue. Don't say anything. Don't tell the user "still watching." Don't acknowledge silence.
   - If non-empty, update `N` to the new total (`wc -l < ~/.meetcoach/current.txt | tr -d ' '`), evaluate the new content against the user's instruction, and:
     - If the criteria match: respond to the user concisely. Address them directly. Quote the trigger line if useful.
     - If not: stay completely silent. No "PASS", no "nothing relevant", no acknowledgement.

4. Loop indefinitely. The user will Ctrl+C when the meeting ends, or tell you to stop.

## Project context for judging "relevance"

You are running inside the user's current project. If their instruction mentions "the project" or "relevant to my work," use the project's `CLAUDE.md` (if present in the cwd or any parent), recent files, and surrounding code to decide what matters. Be strict — when in doubt, stay silent. The user can always loosen by re-invoking with different criteria.

## Tone when you do respond

Short. Direct. Useful. No preamble, no "Based on the transcript…" — just the content. Use bullets for multiple points. If they asked you to draft a reply, give the reply text ready to speak, in quotes.

## Start now

Begin with the baseline read, then enter the watch loop.
