#!/usr/bin/env bash
# Symlink meetcoach's slash commands into ~/.claude/commands/ so edits
# to the repo version flow through immediately. Re-run if the target dir
# changes or after a fresh clone.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
src_dir="$repo_root/share/slash-commands"
dst_dir="$HOME/.claude/commands"

mkdir -p "$dst_dir"

for src in "$src_dir"/*.md; do
    name="$(basename "$src")"
    dst="$dst_dir/$name"
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        echo "  ok    $name (already linked)"
        continue
    fi
    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
        backup="$dst.bak.$(date +%Y%m%d-%H%M%S)"
        mv "$dst" "$backup"
        echo "  moved $name → $(basename "$backup") (existing file preserved)"
    elif [ -L "$dst" ]; then
        rm "$dst"
    fi
    ln -s "$src" "$dst"
    echo "  link  $name → $src"
done
