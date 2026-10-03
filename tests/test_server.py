import json

import pytest
from mcp import Client

from bioharbor.server import build_server

pytestmark = pytest.mark.anyio


def payload(result):
    assert not result.is_error, result
    return result.structured_content or json.loads(result.content[0].text)


async def test_tools_are_listed_with_flat_schemas(harbor):
    async with Client(build_server(harbor)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert {
        "seq_stats",
        "find_orfs",
        "translate_sequence",
        "get_job",
        "gpu_status",
        "read_file",
    } <= set(tools)
    schema = tools["find_orfs"].input_schema
    assert schema["required"] == ["sequence"]
    assert schema["properties"]["min_aa"]["default"] == 50
    assert "ATG" in tools["find_orfs"].description


async def test_call_tool_and_read_output_file(harbor):
    seq = "ATG" + "GCT" * 60 + "TAA"
    async with Client(build_server(harbor)) as client:
        out = payload(await client.call_tool("find_orfs", {"sequence": seq}))
        assert out["status"] == "succeeded"
        path = out["result"]["files"][0]
        content = payload(await client.call_tool("read_file", {"path": path}))
        assert content["content"].startswith(">seq1_orf1")
        jobs = payload(await client.call_tool("list_jobs", {}))
        assert jobs["jobs"][0]["job_id"] == out["job_id"]


async def test_read_file_rejects_escape(harbor):
    async with Client(build_server(harbor)) as client:
        out = payload(await client.call_tool("read_file", {"path": "../../etc/passwd"}))
    assert out["error"] == "InputValidationError"
