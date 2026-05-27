# meetcoach — single interface for setup, dev, and runtime.
# Run `make help` (or just `make`) to see every target.

.DEFAULT_GOAL := help

UNAME := $(shell uname)
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help setup install audio-setup slash-commands register-mcp doctor \
        start listen mcp prompt prompt-copy lint format test clean update status

help:  ## Show this help with all available targets
	@printf "\nmeetcoach — Make targets:\n\n"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@printf "\nFirst-time setup:   make setup\n"
	@printf "Daily use:          make start    (or  make listen  for solo audio)\n"
	@printf "Health check:       make doctor\n\n"

setup: install slash-commands audio-setup  ## First-time bootstrap (install deps + audio driver + slash commands)
	@printf "\nSetup complete. Remaining manual steps:\n\n"
	@printf "  1. Audio MIDI Setup: create a Multi-Output Device with\n"
	@printf "     BlackHole + your usual output (headphones/speakers).\n"
	@printf "     Set it as System Settings > Sound > Output.\n\n"
	@printf "  2. Configure your Deepgram API key:\n"
	@printf "       cp .env.example .env\n"
	@printf "       # then edit .env and paste your key from https://deepgram.com\n\n"
	@printf "  3. Register MCP server with your LLM tool(s):\n"
	@printf "       make register-mcp     # prints the JSON to paste into ~/.claude/settings.json\n\n"
	@printf "  Verify with:               make doctor\n"
	@printf "  Then start a meeting with: make start\n\n"

install:  ## Create the .venv (Python 3.13) and install meetcoach + deps
	@if [ ! -d $(VENV) ]; then uv venv --python 3.13; fi
	uv pip install -e .

audio-setup:  ## Install BlackHole + restart Core Audio (macOS only)
ifeq ($(UNAME),Darwin)
	@if [ -d /Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver ]; then \
	  echo "✓ BlackHole already installed"; \
	else \
	  echo "Installing BlackHole (Homebrew will prompt for admin password)..."; \
	  brew install --cask blackhole-2ch; \
	fi
	@echo "Restarting Core Audio (admin password may be needed)..."
	@sudo killall coreaudiod || true
	@echo "✓ Core Audio refreshed"
else
	@echo "[skip] audio-setup is macOS-only."
	@echo "       Linux audio backend (PulseAudio / PipeWire) is planned but not yet"
	@echo "       shipped. meetcoach will install but live audio capture won't work."
endif

slash-commands:  ## Install /meeting handler into every detected LLM tool
	./scripts/install-slash-commands.sh

register-mcp:  ## Register MCP server with every detected LLM tool (claude / gemini / codex / cursor)
	@python3 ./scripts/register-mcp.py

doctor:  ## Sanity-check the environment (BlackHole, mic, providers, MCP, STT)
	@$(BIN)/meetcoach doctor

status: doctor  ## Alias for `make doctor`

start:  ## Launch the live TUI (transcribes mic + system audio)
	$(BIN)/meetcoach start

listen:  ## Launch the TUI in listen-only mode (no mic — kills mic-bleed echo)
	$(BIN)/meetcoach start --no-mic

mcp:  ## Run the MCP server in the foreground (for debugging — Claude Code normally spawns it)
	$(BIN)/meetcoach-mcp

prompt:  ## Print the /meeting prompt body to stdout
	@$(BIN)/meetcoach prompt

prompt-copy:  ## Copy the /meeting prompt to your clipboard (auto-detects pbcopy/xclip/wl-copy)
	@if command -v pbcopy >/dev/null 2>&1; then \
	  $(BIN)/meetcoach prompt | pbcopy && echo "✓ Copied via pbcopy"; \
	elif command -v xclip >/dev/null 2>&1; then \
	  $(BIN)/meetcoach prompt | xclip -selection clipboard && echo "✓ Copied via xclip"; \
	elif command -v wl-copy >/dev/null 2>&1; then \
	  $(BIN)/meetcoach prompt | wl-copy && echo "✓ Copied via wl-copy"; \
	else \
	  echo "✗ No clipboard tool found (install pbcopy / xclip / wl-copy)."; \
	  exit 1; \
	fi

lint:  ## Run the ruff linter
	$(BIN)/ruff check src/

format:  ## Auto-format source with ruff
	$(BIN)/ruff format src/

test:  ## Run pytest (placeholder — no tests yet)
	@if [ -d tests ]; then $(BIN)/pytest; else echo "No tests yet."; fi

clean:  ## Remove venv, caches, and build artifacts (keeps .env and transcripts/)
	rm -rf $(VENV) .ruff_cache build dist src/meetcoach.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned. Reinstall with: make install"

update:  ## git pull + reinstall (pick up upstream changes)
	git pull
	uv pip install -e .
