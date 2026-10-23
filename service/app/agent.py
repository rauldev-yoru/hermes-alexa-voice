"""Optional bridge between the skill and a local agent CLI.

Disabled by default. When ``agent_bin`` is configured (Hermes works as-is), a
free-text question is handed to the agent and the answer is spoken back.

The one-shot CLI takes 20-25 s, far beyond Alexa's 8 s budget, so the design is
two-phase: answer "on it" immediately, then speak the result through the Echo
once it lands. The speaking is delegated to ``cli/notify.py`` so there is a
single delivery path for both cron notifications and agent answers.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from .config import AGENT_BIN, AGENT_ENABLED, AGENT_PROFILE, AGENT_TIMEOUT, REPO_DIR, STATE_DIR

SESSION_NOTE = re.compile(r"^\s*session_id:.*$", re.MULTILINE)
ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

NOTIFY_PY = REPO_DIR / "cli" / "notify.py"
# notify.py rings the Echo through alexapy, which install.sh only puts in the
# CLI venv — the service's own interpreter cannot import it.
CLI_PYTHON = REPO_DIR / "cli" / "venv" / "bin" / "python"


def _cli_python() -> str:
    return str(CLI_PYTHON) if CLI_PYTHON.is_file() else sys.executable


def _clean(stdout: str) -> str:
    text = ANSI.sub("", stdout)
    text = SESSION_NOTE.sub("", text)
    text = re.sub(r"^\s*(Aviso|Note|Warning):.*$", "", text, flags=re.MULTILINE)
    return text.strip()


def for_speech(text: str, limit: int = 600) -> str:
    """Make agent output speakable: no markdown, no emoji, no paths, no code."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"https?://\S+", "a link", text)
    allowed = set(" .,;:!?¡¿()'-%/°+0123456789")
    text = "".join(ch if (ch.isalnum() or ch in allowed) else " " for ch in text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit]
        stop = max(cut.rfind(". "), cut.rfind("; "))
        text = (cut[: stop + 1] if stop > limit * 0.5 else cut).strip()
    return text


async def ask(prompt: str) -> str:
    """Run one agent turn and return its plain-text answer."""
    if not AGENT_ENABLED or not AGENT_BIN:
        return "No agent is configured for this skill."
    cmd = [AGENT_BIN, "chat", "-q", prompt, "-Q"]
    if AGENT_PROFILE:
        cmd += ["--profile", AGENT_PROFILE]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path.home()),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=AGENT_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "The agent ran out of time. I could not finish."
    text = _clean(out.decode("utf-8", "ignore"))
    if proc.returncode != 0 and not text:
        tail = _clean(err.decode("utf-8", "ignore"))[-300:]
        return f"The agent failed. {tail}"
    return text or "The agent returned nothing."


async def notify_task(task: "asyncio.Task[str]", label: str,
                      device_name: str | None = None) -> None:
    """Attach to an in-flight agent task and speak its answer when it lands."""
    log = STATE_DIR / "agent_runs.log"
    try:
        answer = await task
        spoken = for_speech(answer)
        ok, detail = await speak(spoken, device_name)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{label}] ok={ok} agent={len(answer)}ch spoken={len(spoken)}ch {detail}\n")
    except Exception as exc:  # noqa: BLE001
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{label}] ERROR {type(exc).__name__}: {exc}\n")


async def speak(text: str, device_name: str | None = None) -> tuple[bool, str]:
    """Deliver a spoken message through the CLI, which knows how to ring an Echo."""
    message = for_speech(text)
    if not message:
        return False, "empty message"
    if not NOTIFY_PY.is_file():
        return False, f"{NOTIFY_PY} not found"
    cmd = [_cli_python(), str(NOTIFY_PY), message]
    if device_name:
        cmd += ["--device", device_name]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    ok = proc.returncode == 0
    return ok, (out or err).decode("utf-8", "ignore").strip()[-300:]
