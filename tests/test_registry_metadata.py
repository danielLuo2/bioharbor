import json
from pathlib import Path

import bioharbor

ROOT = Path(__file__).resolve().parents[1]


def test_server_json_matches_package():
    meta = json.loads((ROOT / "server.json").read_text())
    package = meta["packages"][0]
    assert meta["version"] == package["version"] == bioharbor.__version__
    assert package["identifier"] == "bioharbor"
    assert len(meta["description"]) <= 100
    # The registry checks this marker in the PyPI README to verify ownership.
    assert f"mcp-name: {meta['name']} " in (ROOT / "README.md").read_text()
