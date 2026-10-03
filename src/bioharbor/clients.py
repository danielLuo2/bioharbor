"""Connection snippets and config writers for MCP clients (Claude, Cursor, Codex)."""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any

CLIENTS = ("claude-code", "claude-desktop", "cursor", "codex")

# Our slow tools hand back a job_id after ~20 s, well inside every client's limit; this
# only gives Codex (default 60 s) headroom on a busy machine.
CODEX_TOOL_TIMEOUT_S = 120


def server_command() -> tuple[str, list[str]]:
    """Absolute command that starts `bioharbor serve`, so GUI apps that do not inherit
    the shell's PATH (or virtualenv) still find it."""
    exe = shutil.which("bioharbor")
    if exe:
        return str(Path(exe).resolve()), ["serve"]
    return sys.executable, ["-m", "bioharbor", "serve"]


def claude_desktop_config_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home())) / "Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def cursor_config_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"


def json_entry(command: str, args: list[str]) -> dict[str, Any]:
    return {"command": command, "args": args}


def cursor_deeplink(command: str, args: list[str]) -> str:
    config = base64.b64encode(json.dumps(json_entry(command, args)).encode()).decode()
    return f"cursor://anysphere.cursor-deeplink/mcp/install?name=bioharbor&config={config}"


def codex_toml(command: str, args: list[str]) -> str:
    # JSON string escaping is valid TOML basic-string escaping (handles Windows paths).
    return (
        "[mcp_servers.bioharbor]\n"
        f"command = {json.dumps(command)}\n"
        f"args = {json.dumps(args)}\n"
        f"tool_timeout_sec = {CODEX_TOOL_TIMEOUT_S}\n"
    )


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))


def write_json_config(path: Path, command: str, args: list[str]) -> None:
    """Add/replace `mcpServers.bioharbor` in a Claude Desktop or Cursor JSON config."""
    data: dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        _backup(path)
    data.setdefault("mcpServers", {})["bioharbor"] = json_entry(command, args)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


_CODEX_SECTION = re.compile(r"^\[mcp_servers\.bioharbor\][^\n]*\n(?:(?!\[)[^\n]*\n?)*", re.M)


def write_codex_config(path: Path, command: str, args: list[str]) -> None:
    """Add/replace the `[mcp_servers.bioharbor]` table, leaving the rest untouched."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    _backup(path)
    block = codex_toml(command, args)
    if _CODEX_SECTION.search(text):
        text = _CODEX_SECTION.sub(lambda _: block + "\n", text, count=1)
    else:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
