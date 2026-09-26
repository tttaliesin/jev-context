"""Create isolated, restart-verified MCP contexts for the registered-file resume trial."""

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

from jev_context.common import DomainError, digest, dumps, now, uid
from jev_context.engines import fingerprint
from jev_context.policy import Config
from jev_context.service import CONTRACT_V2
from jev_context.shared_engine import SharedLocalEngine

ROOT = Path(__file__).resolve().parents[1]
SOURCE_NAMES = ("case.md", "policy.md", "ui.md")
GOAL = "누락된 등록 파일의 재개 시 탐색 복구"
CONSTRAINTS = [
    "허용된 등록 파일만 현재 상태를 다시 확인하고, 누락 사실과 이전 revision 이력을 보존한다.",
    "data_forget으로 삭제한 원문과 허용되지 않은 경로를 재노출하지 않으며, 읽기 전용 저장소는 수정하지 않는다.",
    "공개 도구 계약을 유지하고, 전체 폴더 자동 수집·작업 원문 삭제·관련 없는 UI 변경은 하지 않는다.",
]
ISSUE = (
    "등록 파일을 디스크에 재생성하는 것만으로 검색이 복원되지 않는다. 아직 코드를 수정하지 않았다."
)
PROGRESS = {
    "summary": "임시 저장소에서 재현했다. 아직 코드를 수정하지 않았다.",
    "next_actions": [
        "등록 파일의 누락 표시와 재관찰 경로를 조사하고 공개 계약을 유지하며 수정한다."
    ],
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def write_json(path, value, *, exclusive=False):
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def sha(path):
    return digest(path.read_bytes())


def implementation_hashes():
    package = ROOT / "src/jev_context"
    return {
        path.name: sha(path)
        for path in sorted(package.iterdir())
        if path.is_file() and path.suffix in {".py", ".json"}
    }


class Report:
    def __init__(self, directory, data):
        self.directory = directory
        self.path = directory / "generation.json"
        self.data = {
            "created_at": now(),
            "phase": "input_setup",
            "passed": False,
            "transport": "official MCP Python SDK / two sequential stdio subprocesses",
            "desktop_current_session_verified": False,
            "model_quality_verified": False,
            "promotion_eligible": False,
            "calls": [],
            "servers": [],
            **data,
        }
        write_json(self.path, self.data, exclusive=True)

    def save(self, **values):
        self.data.update(values)
        write_json(self.path, self.data)

    async def call(self, session, name, **arguments):
        arguments.update(contract_version="2.0", request_id=uid("req"))
        if name in {"source_sync", "work_record"} or "create" in arguments:
            arguments["mutation_id"] = uid("mut")
        validate(arguments, CONTRACT_V2["tools"][name])
        entry = {"phase": self.data["phase"], "tool": name, "arguments": arguments}
        self.data["calls"].append(entry)
        self.save()
        started = time.monotonic()
        try:
            reply = await session.call_tool(name, arguments)
            wire = reply.model_dump(mode="json", by_alias=True, exclude_none=True)
            value = reply.structuredContent
            entry.update(
                elapsed_seconds=time.monotonic() - started,
                response=value,
                wire_response=wire,
                wire_bytes=len(dumps(wire).encode("utf-8")),
            )
            require(isinstance(value, dict), f"{name}: structured response is absent")
            require(
                len(reply.content) == 1
                and reply.content[0].type == "text"
                and json.loads(reply.content[0].text) == value,
                f"{name}: text/structured response mismatch",
            )
            require(value.get("contract_version") == "2.0", "Unexpected contract version")
            require(not reply.isError, f"{name}: {value}")
            return value, entry["elapsed_seconds"]
        except BaseException as exc:
            entry.update(elapsed_seconds=time.monotonic() - started, error=repr(exc))
            raise
        finally:
            self.save()


@asynccontextmanager
async def server(config_path, report):
    number = len(report.data["servers"]) + 1
    arguments = [
        "-B",
        "-X",
        "utf8",
        "-m",
        "jev_context",
        "serve",
        "--config",
        str(config_path),
        "--prepare-engine",
    ]
    record = {
        "number": number,
        "command": sys.executable,
        "arguments": arguments,
        "started_at": now(),
        "stderr_file": f"server-{number}.stderr.txt",
        "sdk_shutdown_completed": False,
    }
    report.data["servers"].append(record)
    report.save()
    params = StdioServerParameters(
        command=sys.executable,
        args=arguments,
        cwd=str(config_path.parent),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    started = time.monotonic()
    try:
        with (report.directory / record["stderr_file"]).open("x", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as streams:
                async with ClientSession(
                    *streams, read_timeout_seconds=timedelta(seconds=15)
                ) as session:
                    initialized = await session.initialize()
                    listing = await session.list_tools()
                    require(
                        {tool.name for tool in listing.tools} == set(CONTRACT_V2["tools"]),
                        "MCP tool inventory differs from contract 2.0",
                    )
                    require(
                        all(
                            tool.inputSchema == CONTRACT_V2["tools"][tool.name]
                            for tool in listing.tools
                        ),
                        "MCP input schemas differ from the frozen local contract",
                    )
                    record.update(
                        startup_seconds=time.monotonic() - started,
                        server_info=initialized.serverInfo.model_dump(mode="json"),
                    )
                    report.save()
                    yield session
                    shutdown_started = time.monotonic()
        # stdio_client closes stdin and awaits its owned subprocess before this point.
        record.update(
            sdk_shutdown_completed=True,
            shutdown_seconds=time.monotonic() - shutdown_started,
            shutdown_basis="official SDK stdio context exited after subprocess shutdown",
        )
    except BaseException as exc:
        record["error"] = repr(exc)
        raise
    finally:
        record["finished_at"] = now()
        report.save()


async def read_source(report, session, source):
    parts, start = [], 1
    while True:
        reply, _ = await report.call(
            session,
            "source_read",
            source_id=source["source_id"],
            revision=source["revision"],
            start_line=start,
            max_bytes=32768,
        )
        require(reply["outcome"] == "ok", "Fixed-revision source read did not complete")
        data = reply["data"]
        for key in ("revision", "content_hash"):
            require(data[key] == source[key], f"Source {key} changed after restart")
        require(data["current_revision"] == source["revision"], "Source became historical")
        parts.append(data["text"])
        if data["eof"]:
            raw = "".join(parts).encode("utf-8")
            require(digest(raw) == source["content_hash"], "Restored source bytes changed")
            return
        require(data["next_start_line"] > start, "Source read did not advance")
        start = data["next_start_line"]


def check_protected(reply, work, policy, policy_raw):
    data = reply["data"]
    checks = {
        "work_revision": data.get("work_revision") == work["revision"],
        "goal": data.get("scope", {}).get("goal") == GOAL,
        "scope": data.get("scope", {}).get("mode") == "implement"
        and data.get("scope", {}).get("constraints") == CONSTRAINTS,
        "constraints": all(
            any(
                item.get("role") == "constraint" and item.get("text") == value
                for item in data.get("protected", [])
            )
            for value in CONSTRAINTS
        ),
        "unresolved_issue": any(
            item.get("role") == "known_issue"
            and item.get("text") == ISSUE
            and item.get("status") == "open"
            for item in data.get("protected", [])
        ),
        "required_policy": any(
            item.get("source_id") == policy["source_id"]
            and item.get("revision") == policy["revision"]
            and item.get("source_hash") == policy["content_hash"]
            and item.get("role") == "required_evidence"
            and item.get("freshness") == "verified_at_read"
            and item.get("text", "").encode("utf-8") == policy_raw
            for item in data.get("evidence", [])
        ),
        "no_missing_required": not data.get("budget", {}).get("missing_required"),
    }
    return checks


async def run(args, report, inputs, profile):
    controller = SharedLocalEngine(profile)
    try:
        report.save(phase="setup", initial_engine=controller.snapshot())
        started = time.monotonic()
        async with server(args.config, report) as session:
            synced, _ = await report.call(
                session,
                "source_sync",
                items=[{"kind": "file", "relative_path": name} for name in SOURCE_NAMES],
            )
            require(synced["outcome"] == "ok", "Source synchronization was not complete")
            items = synced["data"]["items"]
            require(len(items) == 3, "Expected exactly three registered source files")
            sources = {}
            for name, item in zip(SOURCE_NAMES, items, strict=True):
                require(item["content_hash"] == digest(inputs[name]), f"Source changed: {name}")
                sources[name] = item
            created, _ = await report.call(
                session,
                "work_open",
                create={
                    "title": "등록 파일 누락 후 작업 재개",
                    "goal": GOAL,
                    "scope": {"mode": "implement", "constraints": CONSTRAINTS},
                    "origin": {
                        "quote": "실제 등록 파일 복원 결함을 수정하고 세 조건의 작업 재개를 비교한다."
                    },
                },
            )
            work = created["data"]
            for event in [
                {"kind": "issue_opened", "issue_kind": "blocker", "text": ISSUE, "source_refs": []},
                {
                    "kind": "progress_reported",
                    **PROGRESS,
                    "source_refs": [],
                },
            ]:
                recorded, _ = await report.call(
                    session,
                    "work_record",
                    work_id=work["work_id"],
                    expected_revision=work["revision"],
                    event=event,
                )
                work["revision"] = recorded["data"]["revision"]
            opened, _ = await report.call(session, "work_open", work_id=work["work_id"])
            work = opened["data"]
            require(work["goal"] == GOAL and len(work["issues"]) == 1, "Work setup did not persist")
            require(work.get("progress") == PROGRESS, "Work progress was not persisted")
            inspected, _ = await report.call(
                session, "work_inspect", work_id=work["work_id"], view="events", limit=100
            )
            events = inspected["data"]["items"]
            require(not inspected["data"]["next_cursor"], "Setup events were not fully inspected")
            progress_events = [
                event for event in events if event.get("kind") == "progress_reported"
            ]
            require(len(progress_events) == 1, "Expected one actual progress event")
            progress_event = progress_events[0]
            require(
                bool(progress_event.get("event_id"))
                and all(progress_event.get(key) == value for key, value in PROGRESS.items()),
                "The stored progress event ID or payload did not match",
            )
            report.save(work=work, sources=sources, setup_events=events)
        report.save(setup_seconds=time.monotonic() - started, phase="reopen")
        require(report.data["servers"][0]["sdk_shutdown_completed"], "First server did not stop")

        started = time.monotonic()
        async with server(args.config, report) as session:
            reopened, _ = await report.call(session, "work_open", work_id=work["work_id"])
            restored = reopened["data"]
            restored_fields = ("work_id", "goal", "scope", "issues", "progress", "revision")
            for key in restored_fields:
                require(key in restored and key in work, f"Work {key} was absent")
                require(restored[key] == work[key], f"Work {key} changed after restart")
            inspected, _ = await report.call(
                session, "work_inspect", work_id=work["work_id"], view="events", limit=100
            )
            restored_events = inspected["data"]["items"]
            require(
                not inspected["data"]["next_cursor"], "Restored events were not fully inspected"
            )
            require(restored_events == events, "Stored event IDs or payloads changed after restart")
            require(progress_event in restored_events, "The actual progress event was not restored")
            for source in sources.values():
                await read_source(report, session, source)
            report.save(
                reopen_seconds=time.monotonic() - started,
                restart_verified=True,
                restored_fields=list(restored_fields),
                restored_progress_event_id=progress_event["event_id"],
                restored_events=restored_events,
            )
            policy = sources["policy.md"]
            parameters = {
                "work_id": work["work_id"],
                "expected_work_revision": work["revision"],
                "query": GOAL,
                "source_ids": [sources[name]["source_id"] for name in SOURCE_NAMES],
                "required_refs": [
                    {"source_id": policy["source_id"], "revision": policy["revision"]}
                ],
                "budget_bytes": 32768,
                "language": "ko",
            }
            report.save(phase="e1", context_parameters=parameters)
            e1, elapsed = await report.call(
                session, "context_prepare", **parameters, judge_mode="off"
            )
            write_json(args.directory / "e1.json", e1, exclusive=True)
            checks = check_protected(e1, work, policy, inputs["policy.md"])
            report.save(
                e1={
                    "elapsed_seconds": elapsed,
                    "sha256": sha(args.directory / "e1.json"),
                    "outcome": e1["outcome"],
                    "protected_preserved": checks,
                }
            )
            require(
                e1["outcome"] == "ok" and all(checks.values()),
                "E1 did not preserve required context",
            )
            require(e1["data"]["judgment"]["status"] == "skipped", "E1 unexpectedly judged")
            candidate_ids = {
                item["source_id"]
                for item in e1["data"]["evidence"]
                if item["role"] != "required_evidence"
            }
            require(
                {sources["case.md"]["source_id"], sources["ui.md"]["source_id"]} <= candidate_ids,
                "Case and unrelated UI source must both be search candidates; inputs were not changed",
            )

            before = controller.snapshot()
            report.save(phase="preparation", preparation_initial=before, preparation_polls=[])
            started = time.monotonic()
            deadline = started + float(profile.get("prepare_timeout_seconds", 300)) + 10
            try:
                activation = await anyio.to_thread.run_sync(controller.prepare)
                report.save(prepare_response=activation)
            except DomainError as exc:
                report.save(prepare_error={"code": exc.code, "message": str(exc)})
                if exc.code != "engine_preparing":
                    raise
            while True:
                current = await anyio.to_thread.run_sync(controller.snapshot)
                report.data["preparation_polls"].append(
                    {
                        "elapsed_seconds": time.monotonic() - started,
                        "snapshot": current,
                    }
                )
                report.save(preparation_seconds=time.monotonic() - started)
                if current["state"] == "shadow":
                    break
                require(
                    current["state"] in {"idle", "preparing"},
                    f"Model preparation failed: {current}",
                )
                require(
                    time.monotonic() < deadline,
                    "Model preparation exceeded its timeout plus 10 seconds",
                )
                await anyio.sleep(min(5, max(0, deadline - time.monotonic())))
            report.save(
                preparation_seconds=time.monotonic() - started,
                prepared_engine=current,
                preparation_kind="already_ready"
                if before["state"] == "shadow"
                else "requested_preparation",
                preparation_timing_note="Explicit prepare to observed ready; includes up to 5s polling latency; cold/warm cache is in startup metadata.",
            )
            require(current.get("worker_pid") is not None, "Ready model has no worker")

            report.save(phase="e2")
            e2, elapsed = await report.call(
                session, "context_prepare", **parameters, judge_mode="required"
            )
            write_json(args.directory / "e2.json", e2, exclusive=True)
            checks = check_protected(e2, work, policy, inputs["policy.md"])
            judgment = e2["data"].get("judgment", {})

            def evidence_key(item):
                return (item["source_id"], item["revision"], item["content_hash"], item["role"])

            checks["shadow_evidence_preserved"] = sorted(
                map(evidence_key, e1["data"]["evidence"])
            ) == sorted(map(evidence_key, e2["data"]["evidence"]))
            report.save(
                e2={
                    "elapsed_seconds": elapsed,
                    "sha256": sha(args.directory / "e2.json"),
                    "outcome": e2["outcome"],
                    "judgment": judgment,
                    "protected_preserved": checks,
                }
            )
            require(
                e2["outcome"] == "ok" and all(checks.values()),
                "E2 did not preserve required context",
            )
            require(
                judgment.get("status") == "observed" and judgment.get("mode") == "shadow",
                "E2 required judgment was not observed in shadow",
            )
            require(not judgment.get("unjudged_candidates", 0), "E2 has unjudged candidates")
            for name in SOURCE_NAMES:
                require(
                    (args.sources / name).read_bytes() == inputs[name],
                    f"Original input changed: {name}",
                )
                require(
                    (args.directory / "project" / name).read_bytes() == inputs[name],
                    f"Copied input changed: {name}",
                )
            require(sha(args.profile) == report.data["profile"]["sha256"], "Engine profile changed")
            require(sha(args.config) == report.data["config_sha256"], "Isolated config changed")
            require(
                implementation_hashes() == report.data["implementation_hashes"],
                "Service source changed during generation",
            )
        report.save(phase="finished", passed=True)
    finally:
        report.save(
            final_engine_before_proxy_close=controller.snapshot(),
            model_shutdown="Not forced; configured shared-engine idle policy remains in charge",
        )
        controller.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    args.directory = args.directory.resolve()
    args.sources = args.sources.resolve(strict=True)
    args.profile = args.profile.resolve(strict=True)
    profile_raw = args.profile.read_bytes()
    profile = json.loads(profile_raw)
    require(
        profile.get("family") == "semif_openvino",
        "Only the supplied SemIf OpenVINO profile is supported",
    )
    inputs = {name: (args.sources / name).read_bytes() for name in SOURCE_NAMES}
    for name, raw in inputs.items():
        require(bool(raw.decode("utf-8").strip()), f"Empty source: {name}")
    args.directory.mkdir(parents=True, exist_ok=False)
    project = args.directory / "project"
    project.mkdir()
    for name, raw in inputs.items():
        with (project / name).open("xb") as stream:
            stream.write(raw)
    args.config = args.directory / "project.toml"
    config_text = (
        'project_root = "project"\ndata_root = "state"\n'
        f'project_id = "{uid("project")}"\nallowed_paths = ["*.md"]\n'
        'read_only = false\ncontract_version = "2.0"\npolicy_revision = 1\n'
        'timeout_seconds = 8.0\njudgment_seconds = 6.0\n\n[engine]\nstate = "shadow"\n'
        f"profile_file = {json.dumps(str(args.profile))}\n"
    )
    with args.config.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(config_text)
    config = Config.load(args.config)
    require(
        config.project_root.is_relative_to(args.directory)
        and config.data_root.is_relative_to(args.directory),
        "Project or state escaped the output directory",
    )
    report = Report(
        args.directory,
        {
            "config_path": str(args.config),
            "config_sha256": sha(args.config),
            "project_root": str(config.project_root),
            "data_root": str(config.data_root),
            "timeout_seconds": config.timeout_seconds,
            "judgment_seconds": config.judgment_seconds,
            "profile": {
                "path": str(args.profile),
                "sha256": digest(profile_raw),
                "fingerprint": fingerprint(profile),
            },
            "runtime_environment": {
                key: value for key, value in sorted(os.environ.items()) if key.startswith("OV_")
            },
            "source_hashes": {name: digest(raw) for name, raw in inputs.items()},
            "source_directory": str(args.sources),
            "implementation_hashes": implementation_hashes(),
            "script_sha256": sha(Path(__file__)),
            "input_setup_seconds": time.monotonic() - started,
            "prelabels": {
                "provenance": "agent_authored_reference_only_not_scored_or_promoted",
                "case.md": "high relevance",
                "policy.md": "required; high relevance",
                "ui.md": "low relevance",
            },
        },
    )
    try:
        anyio.run(run, args, report, inputs, profile)
    except BaseException as exc:
        report.save(passed=False, failed_phase=report.data["phase"], failure=repr(exc))
    finally:
        report.save(finished_at=now(), total_generation_seconds=time.monotonic() - started)
    print(
        dumps(
            {
                "passed": report.data["passed"],
                "report": str(report.path),
                "e1": report.data.get("e1", {}).get("outcome"),
                "e2": report.data.get("e2", {}).get("outcome"),
                "preparation_seconds": report.data.get("preparation_seconds"),
                "failure": report.data.get("failure"),
            }
        )
    )
    return 0 if report.data["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
