"""A host agent remembers attributed tool results using only the general MCP."""

import json
import subprocess
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jev_context.cli import write_config
from jev_context.policy import Config
from jev_context.service import CONTRACT, CONTRACT_V2


@pytest.mark.parametrize("version", ["1.0", "2.0"])
def test_agent_supplied_result_survives_restart_without_direct_bridge(tmp_path, version):
    root = tmp_path / "project"
    root.mkdir()
    (root / "private.txt").write_text("private-unregistered-marker", encoding="utf-8")
    path = write_config(tmp_path / "project.toml", root, tmp_path / "data", [])
    path.write_text(
        f'contract_version = "{version}"\n' + path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    config = Config.load(path)
    legacy = config.db_path.parent / "workroom" / "workroom-bridge.sqlite"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy-data-must-not-be-opened-migrated-or-deleted")
    before = legacy.read_bytes()
    prefix = {"contract_version": "2.0"} if version == "2.0" else {}
    text = "로그인 오류 수정 보고. 검사는 통과했으나 실제 사용자 환경은 미확인."

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "jev_context", "serve", "--config", str(path)]
        )

        async def call(session, tool, **args):
            response = await session.call_tool(tool, {**prefix, **args})
            assert not response.isError, response
            return response.structuredContent["data"]

        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            listing = await session.list_tools()
            contract = CONTRACT_V2 if version == "2.0" else CONTRACT
            assert {tool.name for tool in listing.tools} == set(contract["tools"])
            status = await call(session, "workspace_status", request_id="status")
            assert status["sources"] == 0
            assert status["engine"]["state"] == "disabled"
            work = await call(
                session,
                "work_open",
                request_id="create",
                mutation_id="create",
                create={
                    "title": "외부 결과 기억",
                    "goal": "확인한 결과와 미검증 한계를 함께 보존",
                    "scope": {"mode": "investigate", "constraints": ["결과 원문과 한계 보존"]},
                    "origin": {"quote": "읽은 결과를 기억해줘"},
                },
            )
            wid = work["work_id"]
            payload = dict(
                mutation_id="remember-result",
                items=[
                    dict(
                        kind="excerpt",
                        text=text,
                        external_key="tool-result:project-a:report-b",
                        origin_label="작업 도구에서 읽은 결과 보고서",
                        origin_locator="project-a/report-b/revision-3",
                    )
                ],
            )
            synced = await call(session, "source_sync", request_id="sync", **payload)
            replay = await call(session, "source_sync", request_id="retry", **payload)
            assert synced["items"] == replay["items"]
            source = synced["items"][0]
            ref = {key: source[key] for key in ("source_id", "revision")}
            updated = await call(
                session,
                "work_record",
                request_id="record",
                mutation_id="record",
                work_id=wid,
                expected_revision=work["revision"],
                event={
                    "kind": "progress_reported",
                    "summary": "도구 결과를 읽어 저장했음. 독립 검증은 미실시.",
                    "next_actions": [],
                    "source_refs": [ref],
                },
            )
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            restored = await call(session, "work_open", request_id="restore", work_id=wid)
            assert restored["revision"] == updated["revision"]
            result = await call(
                session,
                "context_prepare",
                request_id="context",
                work_id=wid,
                query="로그인",
                judge_mode="off",
            )
            assert any(item["source_id"] == ref["source_id"] for item in result["evidence"])
            original = await call(session, "source_read", request_id="original", **ref)
            assert text in original["text"]
            assert original["origin"]["origin_locator"] == "project-a/report-b/revision-3"
            assert original["origin"]["provenance"] == "agent_reported"
            status = await call(session, "workspace_status", request_id="after")
            assert status["sources"] == 1
            assert status["engine"]["state"] == "disabled"
            assert "private-unregistered-marker" not in json.dumps(result)

    anyio.run(scenario)
    assert legacy.read_bytes() == before
    assert not (root / ".codex").exists()


def test_removed_direct_bridge_commands_are_not_available():
    for arguments in (
        ["bridge-config", "--config", "unused"],
        ["serve", "--config", "unused", "--bridge-only"],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "jev_context", *arguments], capture_output=True, timeout=15
        )
        assert result.returncode == 2
        assert b"invalid choice" in result.stderr or b"unrecognized arguments" in result.stderr
