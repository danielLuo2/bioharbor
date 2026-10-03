"""Running external programs (MMseqs2, MAFFT, ...) with timeouts and useful errors."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .errors import ToolExecutionError, ToolUnavailableError
from .registry import RunContext

STDERR_TAIL = 2000


def require_binary(name: str, install_hint: str) -> str:
    """Absolute path of `name`, or a ToolUnavailableError telling the agent how to fix it.

    `BIOHARBOR_<NAME>` (e.g. BIOHARBOR_MMSEQS) overrides the PATH lookup.
    """
    override = os.environ.get(f"BIOHARBOR_{name.upper()}")
    path = override or shutil.which(name)
    if not path or not Path(path).exists():
        raise ToolUnavailableError(f"{name} is not installed", hint=install_hint)
    return path


def run_command(
    cmd: list[str], ctx: RunContext, *, timeout_s: float | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `cmd`, logging it to the job. Raises ToolExecutionError on failure/timeout."""
    ctx.log("$ " + " ".join(cmd))
    timeout = timeout_s or ctx.timeout_s
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd or ctx.workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolExecutionError(
            f"{Path(cmd[0]).name} timed out after {timeout:.0f}s",
            hint="try a smaller input or a less sensitive setting",
        ) from exc
    (ctx.workdir / "stderr.log").write_text(proc.stderr)
    if proc.returncode != 0:
        tail = proc.stderr.strip()[-STDERR_TAIL:]
        raise ToolExecutionError(
            f"{Path(cmd[0]).name} exited with code {proc.returncode}: {tail}",
            hint="see stderr.log in the job directory",
        )
    return proc
