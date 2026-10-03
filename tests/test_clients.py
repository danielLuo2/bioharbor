import base64
import json
import sys

from typer.testing import CliRunner

from bioharbor import clients
from bioharbor.cli import app

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    tomllib = None


def test_json_config_preserves_other_servers(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    clients.write_json_config(cfg, "/opt/bin/bioharbor", ["serve"])
    clients.write_json_config(cfg, "/opt/bin/bioharbor", ["serve"])  # idempotent
    data = json.loads(cfg.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["bioharbor"] == {"command": "/opt/bin/bioharbor", "args": ["serve"]}
    assert (tmp_path / "mcp.json.bak").exists()


def test_codex_config_appends_then_replaces(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('model = "o4"\n\n[mcp_servers.other]\ncommand = "x"\n')
    clients.write_codex_config(cfg, "/old/bioharbor", ["serve"])
    clients.write_codex_config(cfg, r"C:\Users\me\venv\Scripts\bioharbor.exe", ["serve"])
    text = cfg.read_text()
    assert text.count("[mcp_servers.bioharbor]") == 1 and "/old/" not in text
    assert text.startswith('model = "o4"') and "[mcp_servers.other]" in text
    if tomllib:
        data = tomllib.loads(text)
        entry = data["mcp_servers"]["bioharbor"]
        assert entry["command"] == r"C:\Users\me\venv\Scripts\bioharbor.exe"
        assert entry["args"] == ["serve"] and entry["tool_timeout_sec"] == 120
        assert data["mcp_servers"]["other"]["command"] == "x"


def test_codex_section_in_the_middle_is_replaced_in_place(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[mcp_servers.bioharbor]\ncommand = "old"\nargs = []\n\n[profiles.fast]\nmodel = "m"\n'
    )
    clients.write_codex_config(cfg, "new", ["serve"])
    text = cfg.read_text()
    assert '"old"' not in text and '[profiles.fast]\nmodel = "m"' in text
    if tomllib:
        assert tomllib.loads(text)["mcp_servers"]["bioharbor"]["command"] == "new"


def test_cursor_deeplink_round_trips():
    link = clients.cursor_deeplink("/opt/bin/bioharbor", ["serve"])
    assert link.startswith("cursor://anysphere.cursor-deeplink/mcp/install?name=bioharbor&config=")
    config = json.loads(base64.b64decode(link.split("config=", 1)[1]))
    assert config == {"command": "/opt/bin/bioharbor", "args": ["serve"]}


def test_install_cli_shows_every_client_and_writes_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(clients, "cursor_config_path", lambda: tmp_path / ".cursor" / "mcp.json")
    runner = CliRunner()
    shown = runner.invoke(app, ["install"])
    assert shown.exit_code == 0
    for label in ("claude mcp add bioharbor", "Claude Desktop", "Cursor", "codex mcp add"):
        assert label in shown.output
    wrote = runner.invoke(app, ["install", "cursor", "--write"])
    assert wrote.exit_code == 0, wrote.output
    assert "bioharbor" in json.loads((tmp_path / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert runner.invoke(app, ["install", "vim"]).exit_code != 0
