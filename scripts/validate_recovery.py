"""Verify a real project's MCP recovery path without claiming Desktop integration."""

import argparse
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import anyio
from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jev_context.common import digest, dumps, now, uid
from jev_context.engines import fingerprint
from jev_context.policy import Config
from jev_context.service import CONTRACT_V2


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Report:
    def __init__(self, output):
        self.output = output
        output.parent.mkdir(parents=True, exist_ok=True)
        self.data = {
            "run_id": uid("recovery"),
            "created_at": now(),
            "transport": "official MCP Python client / stdio",
            "desktop_current_session_verified": False,
            "phase": "starting",
            "calls": [],
        }
        with output.open("x", encoding="utf-8") as stream:
            json.dump(self.data, stream, ensure_ascii=False, indent=2)

    def save(self, event, **values):
        self.data.update(values)
        record = {"run_id": self.data["run_id"], "at": now(), "event": event, **values}
        with self.output.with_suffix(".jsonl").open("a", encoding="utf-8") as stream:
            stream.write(dumps(record) + "\n")
        self.output.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def call(self, session, name, **arguments):
        arguments.update(contract_version="2.0", request_id=uid("req"))
        if name in {"source_sync", "work_record"} or "create" in arguments:
            arguments["mutation_id"] = uid("mut")
        validate(arguments, CONTRACT_V2["tools"][name])
        entry = {"phase": self.data["phase"], "tool": name, "request": arguments}
        self.data["calls"].append(entry)
        self.save("call_started", latest_call=entry)
        started = time.monotonic()
        try:
            result = await session.call_tool(name, arguments)
            wire = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            value = result.structuredContent
            entry.update(
                elapsed_seconds=time.monotonic() - started,
                result=value,
                wire_result=wire,
                wire_bytes=len(dumps(wire).encode()),
                text_matches_structured=(
                    len(result.content) == 1
                    and result.content[0].type == "text"
                    and json.loads(result.content[0].text) == value
                ),
            )
            require(entry["text_matches_structured"], "Text and structured MCP results differ")
            require(value["contract_version"] == "2.0", "Unexpected contract version")
            require(not result.isError, f"{name} failed: {value}")
            return value
        except Exception as exc:
            entry.update(elapsed_seconds=time.monotonic() - started, error=repr(exc))
            raise
        finally:
            self.save("call_finished", latest_call=entry)


@asynccontextmanager
async def server(config_path, report, prepare):
    arguments = ["-m", "jev_context", "serve", "--config", str(config_path)]
    if prepare:
        arguments.append("--prepare-engine")
    package_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(package_root / "src")
    params = StdioServerParameters(
        command=sys.executable, args=arguments, cwd=str(package_root), env=environment
    )
    report.save("server_starting", server_arguments=arguments)
    async with stdio_client(params) as streams:
        async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=30)) as session:
            initialized = await session.initialize()
            listing = await session.list_tools()
            names = {tool.name for tool in listing.tools}
            require(names == set(CONTRACT_V2["tools"]), "Expected exactly the ten v2 tools")
            require(
                all(tool.inputSchema == CONTRACT_V2["tools"][tool.name] for tool in listing.tools),
                "Server input schemas differ from the local v2 contract",
            )
            report.save(
                "server_connected",
                server_version=initialized.serverInfo.version,
                listed_tools=sorted(names),
                listed_tool_count=len(listing.tools),
            )
            yield session
    report.save("server_stopped")


async def read_source(report, session, source):
    parts, start = [], 1
    while True:
        result = await report.call(
            session,
            "source_read",
            source_id=source["source_id"],
            revision=source["revision"],
            start_line=start,
            max_bytes=32768,
        )
        data = result["data"]
        require(data["revision"] == source["revision"], "Source revision changed")
        require(data["current_revision"] == source["revision"], "Current source revision changed")
        require(data["content_hash"] == source["content_hash"], "Source hash changed")
        parts.append(data["text"])
        if data["eof"]:
            text = "".join(parts)
            require(digest(text.encode()) == source["content_hash"], "Source bytes did not match")
            return text
        require(data["next_start_line"] > start, "Source read did not advance")
        start = data["next_start_line"]


async def run(args, report):
    config = Config.load(args.config)
    require(config.contract_version == "2.0", "Recovery validation requires contract 2.0")
    require(Path(args.source).suffix.lower() == ".md", "--source must name a project Markdown file")
    source_path, source_raw, _ = config.read_file(args.source)
    profile_path = Path(config.engine["profile_file"])
    profile_raw = profile_path.read_bytes()
    profile = json.loads(profile_raw)
    require(profile["family"] != "openjev_modal", "This probe only prepares local engines")
    package = Path(__file__).resolve().parents[1] / "src" / "jev_context"
    report.save(
        "inputs_verified",
        config=str(args.config),
        project_root=str(config.project_root),
        config_sha256=digest(args.config.read_bytes()),
        engine_profile={
            "path": str(profile_path),
            "sha256": digest(profile_raw),
            "fingerprint": fingerprint(profile),
            "family": profile["family"],
        },
        implementation_hashes={
            path.name: digest(path.read_bytes()) for path in package.glob("*.py")
        },
        source_input={"path": source_path, "sha256": digest(source_raw)},
        prior_work_id=args.prior_work_id,
    )
    started = time.monotonic()
    async with server(args.config, report, prepare=True) as session:
        report.save("phase", phase="recovery_and_baseline")
        initial_status = (await report.call(session, "workspace_status"))["data"]
        prior = (await report.call(session, "work_open", work_id=args.prior_work_id))["data"]
        require(not prior.get("redacted"), "The prior work was redacted")
        created = await report.call(
            session,
            "work_open",
            create={
                "title": "경로 이동 복구",
                "goal": "기존 기록 보존 및 사용 경로 검증",
                "scope": {
                    "mode": "implement",
                    "constraints": [
                        "기존 작업과 원문 revision 보존",
                        "모델 관찰과 실제 Desktop 연결 검증을 구분",
                        "새 데이터와 기존 데이터 삭제 금지",
                    ],
                },
                "origin": {"quote": "연결 복구 후 실제 흐름을 검증하고 모델 제외 조건과 비교"},
            },
        )
        work = created["data"]
        report.save("work_created", work_id=work["work_id"], prior_work_restored=True)
        synced = await report.call(
            session, "source_sync", items=[{"kind": "file", "relative_path": args.source}]
        )
        require(synced["outcome"] == "ok", "Source synchronization did not complete")
        source = synced["data"]["items"][0]
        require(source["content_hash"] == digest(source_raw), "Source changed since initial read")
        await read_source(report, session, source)
        recorded = await report.call(
            session,
            "work_record",
            work_id=work["work_id"],
            expected_revision=work["revision"],
            event={
                "kind": "progress_reported",
                "summary": "기존 작업 복원 및 현재 경로의 원문 revision 조회 확인",
                "next_actions": [
                    "동일 질문의 모델 제외·필수 판단 비교",
                    "서버 재시작 후 기록 복원",
                ],
                "source_refs": [{"source_id": source["source_id"], "revision": source["revision"]}],
            },
        )
        work = (await report.call(session, "work_open", work_id=work["work_id"]))["data"]
        require(
            work["revision"] == recorded["data"]["revision"], "Recorded revision did not restore"
        )
        parameters = {
            "work_id": work["work_id"],
            "query": "프로젝트 이동 복구",
            "source_ids": [source["source_id"]],
            "expected_work_revision": work["revision"],
            "budget_bytes": 32768,
            "language": "ko",
        }
        baseline = await report.call(session, "context_prepare", **parameters, judge_mode="off")
        (args.output.parent / "off.json").write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report.save("baseline_saved", source=source, comparison_parameters=parameters)
        report.save("phase", phase="model_preparation")
        if initial_status["engine"]["state"] == "idle":
            # Local model activation is demand-driven; status polling never loads weights.
            activation = await report.call(
                session, "context_prepare", **parameters, judge_mode="required"
            )
            report.save("demand_activation", initial_judgment=activation["data"].get("judgment"))
        status = (await report.call(session, "workspace_status"))["data"]
        while status["engine"]["state"] == "preparing" and time.monotonic() - started < 360:
            await anyio.sleep(min(5, max(0, 360 - (time.monotonic() - started))))
            status = (await report.call(session, "workspace_status"))["data"]
        # Refresh even when the first observation was already ready or unavailable.
        status = (await report.call(session, "workspace_status"))["data"]
        report.save(
            "model_preparation_finished",
            model_preparation_elapsed_seconds=time.monotonic() - started,
            model_ready=status["engine"]["state"] in {"shadow", "active"},
            engine_status=status["engine"],
        )
        report.save("phase", phase="model_comparison")
        try:
            model = await report.call(
                session, "context_prepare", **parameters, judge_mode="required"
            )
            (args.output.parent / "model.json").write_text(
                json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            judgment = model["data"].get("judgment", {})
            report.save(
                "model_saved",
                model_outcome=model["outcome"],
                model_judgment_status=judgment.get("status", "not_returned"),
                model_inference_observed=judgment.get("status") in {"observed", "applied"},
                model_required_satisfied=(
                    model["outcome"] in {"ok", "partial"}
                    and judgment.get("status") in {"observed", "applied"}
                    and not judgment.get("unjudged_candidates", 0)
                ),
            )
        except Exception as exc:
            (args.output.parent / "model.json").write_text(
                json.dumps(
                    {"transport_error": repr(exc), "model_inference_observed": False},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            report.save("model_failed", model_error=repr(exc), model_inference_observed=False)

    report.save("phase", phase="restart_without_model")
    async with server(args.config, report, prepare=False) as session:
        await report.call(session, "workspace_status")
        restored = (await report.call(session, "work_open", work_id=work["work_id"]))["data"]
        prior_restored = (await report.call(session, "work_open", work_id=args.prior_work_id))[
            "data"
        ]
        for key in ("goal", "scope", "revision", "progress"):
            require(restored.get(key) == work.get(key), f"New work {key} changed after restart")
        for key in ("goal", "scope", "revision"):
            require(prior_restored.get(key) == prior.get(key), f"Prior work {key} changed")
        await read_source(report, session, source)
        report.save(
            "restart_verified",
            recovery_verified=True,
            prior_work_preserved=True,
            restored_goal=restored["goal"],
            restored_constraints=restored["scope"]["constraints"],
            restored_source_revision=source["revision"],
        )
    report.save("finished", phase="finished", elapsed_seconds=time.monotonic() - started)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prior-work-id", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    args.config = args.config.resolve()
    args.output = args.output.resolve()
    report = Report(args.output)
    try:
        anyio.run(run, args, report)
    except BaseException as exc:
        report.save("failed", failure=repr(exc), failed_phase=report.data["phase"])
        raise
    print(
        dumps(
            {
                key: value
                for key, value in report.data.items()
                if key not in {"calls", "latest_call"}
            }
        )
    )


if __name__ == "__main__":
    main()
