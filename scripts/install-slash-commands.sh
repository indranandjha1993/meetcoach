#!/usr/bin/env bash
# Install meetcoach's /meeting handler into the on-demand command/skill
# directory of each supported LLM tool. We never write to a tool's
# always-loaded memory file (CLAUDE.md / GEMINI.md / .cursorrules / AGENTS.md)
# so there's no context pollution.
#
# Usage:
#   ./install-slash-commands.sh                # install to every detected platform
#   ./install-slash-commands.sh --platform claude     # just Claude Code
#   ./install-slash-commands.sh --platform cursor     # just Cursor
#   ./install-slash-commands.sh --platform gemini     # just Gemini CLI
#   ./install-slash-commands.sh --platform codex      # just Codex CLI
#   ./install-slash-commands.sh --list                # show what's detected, don't install

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
claude_src="$repo_root/share/slash-commands/meeting.md"
skill_src_dir="$repo_root/share/skills/meeting"

# Detection: platform → marker dir → target install path
# We only install to user-scoped on-demand directories. Project-scoped or
# memory-file paths are deliberately skipped.
detect_target() {
    local platform="$1"
    case "$platform" in
        claude)
            [ -d "$HOME/.claude" ] && echo "$HOME/.claude/commands/meeting.md"
            ;;
        cursor)
            [ -d "$HOME/.cursor" ] && echo "$HOME/.cursor/skills-cursor/meeting"
            ;;
        gemini)
            [ -d "$HOME/.gemini" ] && echo "$HOME/.gemini/skills/meeting"
            ;;
        codex)
            [ -d "$HOME/.codex" ] && echo "$HOME/.codex/skills/meeting"
            ;;
    esac
}

source_for() {
    local platform="$1"
    case "$platform" in
        claude) echo "$claude_src" ;;
        cursor|gemini|codex) echo "$skill_src_dir" ;;
    esac
}

# Atomic install: back up an existing non-symlink, then symlink the source in.
install_one() {
    local platform="$1"
    local src="$(source_for "$platform")"
    local dst="$(detect_target "$platform")"

    if [ -z "$dst" ]; then
        echo "  skip  $platform     (not detected on this machine)"
        return 0
    fi

    if [ ! -e "$src" ]; then
        echo "  err   $platform     (missing source: $src)" >&2
        return 1
    fi

    mkdir -p "$(dirname "$dst")"

    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        echo "  ok    $platform     (already linked → $dst)"
        return 0
    fi

    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
        local backup="$dst.bak.$(date +%Y%m%d-%H%M%S)"
        mv "$dst" "$backup"
        echo "  moved $platform     (existing file → $(basename "$backup"))"
    elif [ -L "$dst" ]; then
        rm "$dst"
    fi

    ln -s "$src" "$dst"
    echo "  link  $platform     ($dst → $src)"
}

platforms_all=(claude cursor gemini codex)

selected="all"
list_only=false

while [ $# -gt 0 ]; do
    case "$1" in
        --platform)
            selected="${2:-}"
            shift 2
            ;;
        --platform=*)
            selected="${1#*=}"
            shift
            ;;
        --list)
            list_only=true
            shift
            ;;
        -h|--help)
            sed -n '2,15p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

if $list_only; then
    echo "meetcoach /meeting installation targets:"
    for p in "${platforms_all[@]}"; do
        dst="$(detect_target "$p")"
        if [ -z "$dst" ]; then
            echo "  -  $p  (not detected; tool not installed?)"
        elif [ -L "$dst" ] && [ "$(readlink "$dst")" = "$(source_for "$p")" ]; then
            echo "  ✓  $p  (installed: $dst)"
        else
            echo "  ✗  $p  (would install to: $dst)"
        fi
    done
    exit 0
fi

if [ "$selected" = "all" ]; then
    platforms=("${platforms_all[@]}")
else
    case " ${platforms_all[*]} " in
        *" $selected "*) platforms=("$selected") ;;
        *)
            echo "Unknown platform: $selected (valid: ${platforms_all[*]}, all)" >&2
            exit 2
            ;;
    esac
fi

failed=0
for p in "${platforms[@]}"; do
    install_one "$p" || failed=1
done

exit "$failed"
