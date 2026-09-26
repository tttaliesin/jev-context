"""Real model + actual MCP stdio in a disposable project, without host verification claims."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jev_context.common import now, uid


async def run(profile, output):
    with tempfile.TemporaryDirectory(prefix="jev-v2-smoke-") as directory:
        root = Path(directory)
        (root / "rules.md").write_text(
            "로그인 실패의 원인은 토큰 만료가 아니라 데이터베이스 연결 오류다.\n", encoding="utf-8"
        )
        config = root / "project.toml"
        config.write_text(
            "\n".join(
                [
                    f"project_root = {json.dumps(str(root))}",
                    f"data_root = {json.dumps(str(root / 'data'))}",
                    f"project_id = {json.dumps(uid('project'))}",
                    'allowed_paths = ["*.md"]',
                    'contract_version = "2.0"',
                    "[engine]",
                    'state = "shadow"',
                    f"profile_file = {json.dumps(str(profile))}",
                ]
            ),
            encoding="utf-8",
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "jev_context", "serve", "--config", str(config), "--prepare-engine"],
        )
        report = {
            "created_at": now(),
            "transport": "official MCP Python client / stdio",
            "desktop_current_session_verified": False,
            "calls": [],
        }
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            initialized = await session.initialize()
            report["server_version"] = initialized.serverInfo.version
            listing = await session.list_tools()
            assert len(listing.tools) == 10

            async def call(name, **args):
                args.update(contract_version="2.0", request_id=uid("req"))
                if name in {"source_sync", "work_record", "data_forget"} or "create" in args:
                    args["mutation_id"] = uid("mut")
                result = await session.call_tool(name, args)
                value = result.structuredContent
                assert json.loads(result.content[0].text) == value
                assert value["contract_version"] == "2.0"
                assert not result.isError, value
                report["calls"].append({"tool": name, "result": value})
                return value["data"]

            status = await call("workspace_status")
            assert status["engine"]["state"] == "shadow"
            work = await call(
                "work_open",
                create={
                    "title": "모델 연결 검사",
                    "goal": "로그인 오류 조사",
                    "scope": {"mode": "investigate", "constraints": ["반대 근거 보존"]},
                    "origin": {"quote": "격리된 통합 테스트"},
                },
            )
            wid = work["work_id"]
            sources = await call(
                "source_sync", items=[{"kind": "file", "relative_path": "rules.md"}]
            )
            source = sources["items"][0]
            await call(
                "source_read",
                source_id=source["source_id"],
                revision=source["revision"],
                start_line=1,
                max_bytes=8192,
            )
            context = await call(
                "context_prepare",
                work_id=wid,
                query="로그인 오류 원인",
                claim="로그인 오류는 토큰 만료 때문에 발생한다",
                budget_bytes=16384,
                judge_mode="required",
            )
            assert context["judgment"]["status"] == "observed", context
            assert context["evidence"]
            report["real_model_inference"] = context["judgment"]
            capabilities = await call(
                "capability_recommend",
                work_id=wid,
                query="로그인 오류 원인 조사",
                inventory={
                    "revision": "smoke-1",
                    "observed_at": now(),
                    "complete": True,
                    "items": [
                        {
                            "id": "read-source",
                            "kind": "tool",
                            "description": "현재 소스 코드와 오류 로그 읽기",
                            "version": "1",
                            "available": True,
                            "mandatory": True,
                        }
                    ],
                },
            )
            assert capabilities["selected"][0]["mandatory"]
            await call(
                "handoff_prepare",
                work_id=wid,
                expected_work_revision=1,
                query="로그인",
                role="독립 원인 조사",
                round=0,
                mode="read",
                budget_bytes=16384,
            )
            await call(
                "work_record",
                work_id=wid,
                expected_revision=1,
                event={
                    "kind": "criterion_registered",
                    "criterion_id": "regression",
                    "description": "수정 후 회귀 검사",
                    "target_revision": "source-1",
                    "required": True,
                    "source_refs": [],
                },
            )
            inspected = await call("work_inspect", work_id=wid, view="criteria")
            assert inspected["items"][0]["status"] == "not_run"
            status = await call("workspace_status")
            await call(
                "data_forget",
                expected_data_revision=status["data_revision"],
                target={"kind": "work", "work_id": wid},
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "server_version": report["server_version"],
                    "unique_tools_called": len(set(c["tool"] for c in report["calls"])),
                    "real_model_status": report["real_model_inference"]["status"],
                    "output": str(output),
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    anyio.run(run, args.profile.resolve(), args.output.resolve())
