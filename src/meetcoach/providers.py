"""LLM coach providers — pluggable CLI backends.

Each provider wraps an LLM CLI binary (`claude`, `gemini`, `codex`) so the
Coach loop can route invocations through any of them. New providers are
trivial to add: subclass `CoachProvider`, implement `is_available()` and
`_build_command()`, and register in `PROVIDER_CLASSES`.

Conventions:

- `system_prompt` is the agent's instructions (cached / role-defining).
  Each provider prepends or passes it through whichever flag the CLI
  supports — for CLIs without a system-prompt flag, we merge it into
  the user prompt with a clear delimiter.
- `prompt` is the per-call content (the rolling transcript + ask).
- `model` is optional; pass-through to the provider's `--model` arg.

All providers return either the model's text response, or an `[error: ...]`
string the caller can surface in the coach pane.
"""
from __future__ import annotations

import asyncio
import contextlib
import shutil
from abc import ABC, abstractmethod


class CoachProvider(ABC):
    """One coach LLM provider, wrapping a CLI binary."""

    name: str = ""  # short id used in --coach-provider
    default_binary: str = ""  # binary name to look for on PATH
    install_hint: str = ""  # short instructions if missing
    supports_system_prompt: bool = False

    def __init__(self, binary: str | None = None, model: str | None = None) -> None:
        self.binary = binary or self.default_binary
        self.model = model

    @classmethod
    def is_available(cls, binary: str | None = None) -> bool:
        return shutil.which(binary or cls.default_binary) is not None

    @classmethod
    def resolved_path(cls, binary: str | None = None) -> str | None:
        return shutil.which(binary or cls.default_binary)

    @abstractmethod
    def _command_and_stdin(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[list[str], bytes | None]:
        """Return (argv, stdin_bytes). stdin_bytes=None means don't pipe stdin."""

    @staticmethod
    def _merged_prompt(system_prompt: str, user_prompt: str) -> str:
        return f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"

    async def invoke(
        self, system_prompt: str, user_prompt: str, timeout_s: int = 60
    ) -> str:
        bin_path = shutil.which(self.binary) or self.binary
        if not shutil.which(self.binary):
            return (
                f"[coach unavailable: {self.name} CLI not found at {bin_path!r}. "
                f"Install: {self.install_hint}]"
            )
        cmd, stdin_bytes = self._command_and_stdin(system_prompt, user_prompt)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return (
                f"[coach unavailable: {self.name} CLI not found at {bin_path!r}. "
                f"Install: {self.install_hint}]"
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin_bytes), timeout=timeout_s
            )
        except TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            return f"[error: {self.name} CLI timed out after {timeout_s}s]"

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()[:200]
            return f"[error: {self.name} exited {proc.returncode}: {err}]"
        return stdout.decode(errors="replace")


class ClaudeCli(CoachProvider):
    """Anthropic's `claude` CLI (Claude Code). Uses your Claude Max plan;
    no API key needed. System prompt via --append-system-prompt, user prompt
    via stdin."""

    name = "claude"
    default_binary = "claude"
    install_hint = "https://docs.claude.com/en/docs/claude-code"
    supports_system_prompt = True

    def _command_and_stdin(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[list[str], bytes | None]:
        bin_path = shutil.which(self.binary) or self.binary
        cmd = [
            bin_path,
            "-p",
            "--output-format",
            "text",
            "--append-system-prompt",
            system_prompt,
        ]
        if self.model:
            cmd += ["--model", self.model]
        return cmd, user_prompt.encode()


class GeminiCli(CoachProvider):
    """Google's `gemini` CLI (`@google/gemini-cli`, install via npm).

    Full prompt is passed via `-p` (Gemini's non-interactive headless flag).
    Stdin is not used because Gemini's `-p` + stdin combo appends stdin to
    the prompt, which gives less predictable behavior than embedding the
    whole prompt in `-p`.
    """

    name = "gemini"
    default_binary = "gemini"
    install_hint = "npm install -g @google/gemini-cli"
    supports_system_prompt = False

    def _command_and_stdin(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[list[str], bytes | None]:
        bin_path = shutil.which(self.binary) or self.binary
        merged = self._merged_prompt(system_prompt, user_prompt)
        cmd = [bin_path, "-p", merged, "-o", "text"]
        if self.model:
            cmd += ["-m", self.model]
        return cmd, None


class CodexCli(CoachProvider):
    """OpenAI's `codex` CLI (`@openai/codex`, install via npm).

    `codex exec` runs non-interactively; instructions are read from stdin
    when no positional prompt is provided.
    """

    name = "codex"
    default_binary = "codex"
    install_hint = "npm install -g @openai/codex"
    supports_system_prompt = False

    def _command_and_stdin(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[list[str], bytes | None]:
        bin_path = shutil.which(self.binary) or self.binary
        cmd = [bin_path, "exec"]
        if self.model:
            cmd += ["-c", f"model={self.model!r}"]
        merged = self._merged_prompt(system_prompt, user_prompt)
        return cmd, merged.encode()


PROVIDER_CLASSES: dict[str, type[CoachProvider]] = {
    "claude": ClaudeCli,
    "gemini": GeminiCli,
    "codex": CodexCli,
}


def list_provider_names() -> list[str]:
    return list(PROVIDER_CLASSES.keys())


def get_provider(
    name: str, binary: str | None = None, model: str | None = None
) -> CoachProvider:
    name = (name or "claude").lower()
    cls = PROVIDER_CLASSES.get(name)
    if cls is None:
        raise ValueError(
            f"unknown coach provider {name!r}. Available: {', '.join(list_provider_names())}"
        )
    return cls(binary=binary, model=model)


def detect_available_providers() -> list[tuple[str, bool, str | None]]:
    """Return (name, is_available, resolved_path) for each provider class."""
    out: list[tuple[str, bool, str | None]] = []
    for name, cls in PROVIDER_CLASSES.items():
        path = cls.resolved_path()
        out.append((name, path is not None, path))
    return out
