import json
import subprocess
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jev_context.cli import write_config


def test_real_mcp_client_restart_and_all_tools(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "rules.md").write_text("권한: 설계만 작성\n", encoding="utf-8")
    config = write_config(tmp_path / "config.toml", root, tmp_path / "data", ["*.md"])

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "jev_context", "serve", "--config", str(config)]
        )
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            result = await session.initialize()
            assert result.serverInfo.name == "jev-context"
            listing = await session.list_tools()
            assert len(listing.tools) == 11
            assert all(t.outputSchema for t in listing.tools if not t.name.startswith("bridge_"))
            status = await session.call_tool("workspace_status", {"request_id": "status"})
            assert status.structuredContent["data"]["sources"] == 0
            created = await session.call_tool(
                "work_open",
                dict(
                    request_id="create",
                    mutation_id="create",
                    create=dict(
                        title="한국어 작업",
                        goal="설계",
                        scope=dict(mode="design", constraints=["설계만"]),
                        origin=dict(quote="설계만 해줘"),
                    ),
                ),
            )
            assert not created.isError
            assert json.loads(created.content[0].text) == created.structuredContent
            wid = created.structuredContent["data"]["work_id"]
            synced = await session.call_tool(
                "source_sync",
                dict(
                    request_id="sync",
                    mutation_id="sync",
                    items=[dict(kind="file", relative_path="rules.md")],
                ),
            )
            item = synced.structuredContent["data"]["items"][0]
            read = await session.call_tool(
                "source_read",
                dict(request_id="read", source_id=item["source_id"], revision=item["revision"]),
            )
            assert "권한" in read.structuredContent["data"]["text"]
            recorded = await session.call_tool(
                "work_record",
                dict(
                    request_id="record",
                    mutation_id="record",
                    work_id=wid,
                    expected_revision=1,
                    event=dict(
                        kind="progress_reported", summary="조사 중", next_actions=[], source_refs=[]
                    ),
                ),
            )
            assert recorded.structuredContent["data"]["revision"] == 2
            inspected = await session.call_tool(
                "work_inspect", dict(request_id="inspect", work_id=wid, view="events")
            )
            assert len(inspected.structuredContent["data"]["items"]) == 2
            invalid = await session.call_tool("workspace_status", {"request_id": "bad", "extra": 1})
            assert (
                invalid.isError and invalid.structuredContent["error"]["code"] == "invalid_argument"
            )
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            restored = await session.call_tool(
                "work_open", {"request_id": "restore", "work_id": wid}
            )
            assert restored.structuredContent["data"]["revision"] == 2
            packet = await session.call_tool(
                "context_prepare", {"request_id": "context", "work_id": wid, "query": "권한"}
            )
            assert packet.structuredContent["data"]["evidence"]
            status = await session.call_tool("workspace_status", {"request_id": "status2"})
            deleted = await session.call_tool(
                "data_forget",
                dict(
                    request_id="delete",
                    mutation_id="delete",
                    target=dict(kind="work", work_id=wid),
                    expected_data_revision=status.structuredContent["data"]["data_revision"],
                ),
            )
            assert not deleted.isError

    anyio.run(scenario)


def test_duplicate_json_keys_rejected_at_transport(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    config = write_config(tmp_path / "config.toml", root, tmp_path / "data", [])
    process = subprocess.Popen(
        [sys.executable, "-m", "jev_context", "serve", "--config", str(config)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output, _ = process.communicate(
        b'{"jsonrpc":"2.0","id":1,"id":2,"method":"initialize"}\n', timeout=15
    )
    messages = [json.loads(line) for line in output.splitlines()]
    assert messages[0]["error"]["code"] == -32700
