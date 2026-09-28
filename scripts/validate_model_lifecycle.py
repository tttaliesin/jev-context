"""Exercise an independently started, real local broker through two MCP transports."""

import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jev_context.common import now, uid
from jev_context.judgment import question
from jev_context.policy import Config
from jev_context.shared_engine import SharedLocalEngine

if __package__:
    from scripts.prepare_laya_training_data import VERIFICATION_MARKER, audit_records, digest
else:
    from prepare_laya_training_data import VERIFICATION_MARKER, audit_records, digest


@asynccontextmanager
async def server(config):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jev_context", "serve", "--config", str(config), "--prepare-engine"],
        env=dict(os.environ),
    )
    async with stdio_client(params) as streams:
        async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=10)) as session:
            await session.initialize()
            assert len((await session.list_tools()).tools) == 10
            yield session


async def run(args):
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    config = Config.load(args.config)
    profile = json.loads(Path(config.engine["profile_file"]).read_text(encoding="utf-8"))
    controller = SharedLocalEngine(profile)
    report = {
        "work_id": args.work_id,
        "profile_fingerprint": controller.fingerprint,
        "calls": [],
        "preparations": [],
        "repeat_after_idle": args.repeat_after_idle,
        "require_cache_hit": args.require_cache_hit,
        "timing_note": "Demand-to-ready and idle-release times include status polling latency.",
        "desktop_current_session": False,
    }

    def save(**values):
        report.update(values)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async def call(session, tool, **arguments):
        arguments.update(contract_version="2.0", request_id=uid("req"))
        if tool == "source_sync":
            arguments["mutation_id"] = uid("mut")
        start = time.monotonic()
        reply = await session.call_tool(tool, arguments)
        value = reply.structuredContent
        report["calls"].append(
            {
                "tool": tool,
                "arguments": arguments,
                "elapsed_seconds": time.monotonic() - start,
                "response": value,
            }
        )
        save()
        assert not reply.isError, value
        return value

    async def refresh_work(session):
        refreshed = (await call(session, "work_open", work_id=args.work_id))["data"]
        assert refreshed["goal"] == work["goal"] and refreshed["scope"] == work["scope"]
        parameters["expected_work_revision"] = refreshed["revision"]

    async def demand_ready(requester, observer, *, repeat=False):
        await refresh_work(requester)
        started = time.monotonic()
        warming = await call(requester, "context_prepare", **parameters, judge_mode="required")
        assert warming["outcome"] == "insufficient", warming
        assert time.monotonic() - started < 8
        deadline = time.monotonic() + profile.get("prepare_timeout_seconds", 300) + 10
        while time.monotonic() < deadline:
            state = (await call(observer, "workspace_status"))["data"]["engine"]
            if state["state"] == "shadow":
                break
            assert state["state"] == "preparing", state
            await anyio.sleep(5)
        else:
            raise AssertionError("Model did not prepare within its deadline")
        prepared = controller.snapshot()
        assert prepared["worker_pid"] is not None, prepared
        assert all(prepared[key] == initial[key] for key in ("instance_id", "broker_pid"))
        elapsed = time.monotonic() - started
        report["preparations"].append(
            {
                "after_idle": repeat,
                "demand_to_ready_observed_seconds": elapsed,
                "work_revision": parameters["expected_work_revision"],
                "snapshot": prepared,
                "startup": prepared.get("startup"),
            }
        )
        save()
        startup = prepared.get("startup")
        assert isinstance(startup, dict), "Ready worker did not expose startup metadata"
        if args.require_cache_hit and (repeat or not args.repeat_after_idle):
            assert startup.get("loaded_from_cache") is True, startup
        return prepared, elapsed

    async def observed(session, prepared):
        await refresh_work(session)
        answer = await call(session, "context_prepare", **parameters, judge_mode="required")
        assert answer["data"]["judgment"]["status"] == "observed", answer
        current = controller.snapshot()
        assert all(
            current[key] == prepared[key] for key in ("instance_id", "broker_pid", "worker_pid")
        ), current

    async def wait_idle(session, prepared):
        started = time.monotonic()
        while time.monotonic() - started < prepared["idle_timeout_seconds"] + 10:
            current = (await call(session, "workspace_status"))["data"]["engine"]
            if current["state"] == "idle" and current["worker_pid"] is None:
                assert current["persistent"], current
                assert all(
                    current[key] == prepared[key] for key in ("instance_id", "broker_pid")
                ), current
                return current, time.monotonic() - started
            await anyio.sleep(5)
        raise AssertionError("Status-only traffic prevented model idle release")

    try:
        initial = controller.snapshot()
        assert (
            initial.get("persistent")
            and initial["state"] == "idle"
            and initial["worker_pid"] is None
        ), initial
        save(phase="two_idle_mcp_hosts", initial=initial)
        async with server(args.config) as survivor:
            async with server(args.config) as first:
                for session in (first, survivor):
                    status = (await call(session, "workspace_status"))["data"]["engine"]
                    assert status["state"] == "idle" and status["worker_pid"] is None
                    work = (await call(session, "work_open", work_id=args.work_id))["data"]
                synced = await call(
                    first,
                    "source_sync",
                    items=[{"kind": "file", "relative_path": "docs/model-lifecycle-case.md"}],
                )
                source = synced["data"]["items"][0]
                parameters = {
                    "work_id": args.work_id,
                    "expected_work_revision": work["revision"],
                    "query": "모델 수명 검증",
                    "source_ids": [source["source_id"]],
                    "budget_bytes": 65536,
                    "language": "ko",
                }
                for session in (first, survivor):
                    off = await call(session, "context_prepare", **parameters, judge_mode="off")
                    assert off["data"]["judgment"]["status"] == "skipped"
                    assert controller.snapshot()["worker_pid"] is None
                save(
                    unused_hosts_start_zero_workers=True, phase="demand_preparation", source=source
                )
                prepared, ready_seconds = await demand_ready(first, survivor)
                save(
                    phase="two_clients_inference",
                    cold_ready_seconds=ready_seconds,
                    prepared=prepared,
                )
                for session in (first, survivor):
                    await observed(session, prepared)
            await observed(survivor, prepared)
            save(one_mcp_close_preserves_other_inference=True, phase="idle_release")
            current, idle_seconds = await wait_idle(survivor, prepared)
            save(
                idle_release_seconds=idle_seconds,
                idle_status=current,
                status_does_not_keep_model_alive=True,
            )
            if args.repeat_after_idle:
                save(phase="repeat_demand_preparation")
                async with server(args.config) as second:
                    status = (await call(second, "workspace_status"))["data"]["engine"]
                    assert status["state"] == "idle" and status["worker_pid"] is None, status
                    repeated, ready_seconds = await demand_ready(second, survivor, repeat=True)
                    assert repeated["worker_pid"] != prepared["worker_pid"], repeated
                    save(
                        phase="repeat_two_clients_inference",
                        repeat_ready_seconds=ready_seconds,
                        repeat_prepared=repeated,
                        idle_request_started_new_worker=True,
                    )
                    for session in (second, survivor):
                        await observed(session, repeated)
                await observed(survivor, repeated)
                save(repeat_two_clients_observed=True, phase="repeat_idle_release")
                current, idle_seconds = await wait_idle(survivor, repeated)
                save(repeat_idle_release_seconds=idle_seconds, repeat_idle_status=current)
            save(phase="finished", passed=True)
    except BaseException as exc:
        save(passed=False, failure=repr(exc))
        raise
    finally:
        controller.close()
    print(
        json.dumps({k: v for k, v in report.items() if k != "calls"}, ensure_ascii=False, indent=2)
    )


async def verify_storage(args):
    """Real stdio + local model; this is explicitly not the current desktop connection."""
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("Use a new output path to preserve earlier verification evidence")
    config = Config.load(args.config)
    report = {"desktop_current_session": False, "usage": "verification_only", "calls": []}

    def save(**values):
        report.update(values)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async def call(session, tool, **arguments):
        arguments.update(contract_version="2.0", request_id=uid("req"))
        if tool in {"source_sync", "work_open"} and "work_id" not in arguments:
            arguments["mutation_id"] = uid("mut")
        sent_at = now()
        reply = await session.call_tool(tool, arguments)
        value = reply.structuredContent
        report["calls"].append(
            {
                "tool": tool,
                "arguments": arguments,
                "sent_at": sent_at,
                "received_at": now(),
                "response": value,
            }
        )
        save()
        assert not reply.isError and value["outcome"] not in {"error", "conflict"}, value
        return value["data"]

    def stored(packet_id):
        with sqlite3.connect(config.db_path.as_uri() + "?mode=ro", uri=True) as db:
            (body,) = db.execute("SELECT body FROM packets WHERE id=?", (packet_id,)).fetchone()
        return json.loads(body)

    async def check_packet(session, packet_id, expected_state, purposes, call_times):
        body = stored(packet_id)
        rows = body["judgment"]["evaluations"]
        assert len(rows) == 1, rows
        item = rows[0]
        request = item["request"]
        assert request["state"] == expected_state, request
        assert request["questions"] == [question(p) for p in purposes]
        assert request["profile_fingerprint"] == first_status["engine"]["profile_fingerprint"]
        assert request["project_id"] == config.project_id and request["work_id"] == work["work_id"]
        assert item["request_dispatched"] and item["input_hash"] == digest(expected_state)
        assert call_times[0] <= item["captured_at"] <= call_times[1]
        assert item["status"] == "observed", item
        assert {a["question_id"] for a in item["answers"]} == set(purposes)
        inspected = await call(
            session, "work_inspect", work_id=work["work_id"], view="judgments", packet_id=packet_id
        )
        assert inspected["items"] == rows and inspected["next_cursor"] is None
        return body

    try:
        async with server(args.config) as first:
            first_status = await call(first, "workspace_status")
            assert first_status["runtime"]["code_state"] == "matches_disk", first_status["runtime"]
            assert first_status["engine"]["worker_pid"] is None, "Another model is already running"
            work = await call(
                first,
                "work_open",
                create={
                    "title": "[검증 전용] 세 목적 요청 저장과 재시작 복원",
                    "goal": "실제 MCP 요청의 입력·질문·결과 저장과 재시작 복원을 검증한다.",
                    "origin": {
                        "quote": "검증용 요청은 따로 표시하고 실제 업무 자료나 독립 성능평가에 포함하지 마."
                    },
                    "scope": {
                        "mode": "investigate",
                        "constraints": [VERIFICATION_MARKER],
                        "allowed_actions": ["read", "run_checks"],
                    },
                },
            )
            save(work_id=work["work_id"], phase="prepare")
            synced = await call(
                first,
                "source_sync",
                items=[{"kind": "file", "relative_path": "docs/model-lifecycle-case.md"}],
            )
            source = synced["items"][0]
            parameters = {
                "work_id": work["work_id"],
                "expected_work_revision": work["revision"],
                "query": "사용하지 않는 연결의 모델 점유 문제와 검증 조건은 무엇인가?",
                "claim": "이 기록은 모델 품질이 향상됐다는 실측 결과다.",
                "source_ids": [source["source_id"]],
                "budget_bytes": 65536,
                "language": "ko",
                "judge_mode": "required",
            }
            await call(first, "context_prepare", **parameters)
            profile = json.loads(Path(config.engine["profile_file"]).read_text("utf-8"))
            deadline = time.monotonic() + profile.get("prepare_timeout_seconds", 300) + 10
            while True:
                status = await call(first, "workspace_status")
                if status["engine"]["state"] == "shadow":
                    break
                assert status["engine"]["state"] in {"idle", "preparing"}, status["engine"]
                assert time.monotonic() < deadline, "Model preparation timed out"
                await anyio.sleep(5)
            save(phase="request_checks", model_ready=status["engine"])
            context = await call(first, "context_prepare", **parameters)
            times = report["calls"][-1]
            body = stored(context["packet_id"])
            candidate = next(e for e in body["evidence"] if e["source_id"] == source["source_id"])
            assert candidate["text"] == (
                config.project_root / "docs/model-lifecycle-case.md"
            ).read_text(encoding="utf-8")
            state = {
                "query": parameters["query"],
                "goal": work["goal"],
                "constraints": work["scope"]["constraints"],
                "candidate": candidate["text"],
                "claim": parameters["claim"],
            }
            before = {
                context["packet_id"]: await check_packet(
                    first,
                    context["packet_id"],
                    state,
                    ["relevance", "evidence_relation"],
                    (times["sent_at"], times["received_at"]),
                )
            }
            available = next(
                t for t in (await first.list_tools()).tools if t.name == "workspace_status"
            )
            inventory = {
                "complete": False,
                "observed_at": now(),
                "revision": uid("inventory"),
                "items": [
                    {
                        "id": available.name,
                        "kind": "tool",
                        "description": available.description,
                        "version": "2.0",
                        "available": True,
                        "mandatory": False,
                    }
                ],
            }
            query = "현재 프로젝트의 모델 실행 상태를 읽어서 확인할 수 있는 도구인가?"
            recommended = await call(
                first,
                "capability_recommend",
                work_id=work["work_id"],
                query=query,
                inventory=inventory,
                language="ko",
                budget_bytes=32768,
            )
            times = report["calls"][-1]
            pid = recommended["inspection"]["packet_id"]
            state = {
                "query": query,
                "goal": work["goal"],
                "scope": work["scope"],
                "candidate": inventory["items"][0],
            }
            before[pid] = await check_packet(
                first, pid, state, ["capability_fit"], (times["sent_at"], times["received_at"])
            )
            assert recommended["evaluations"] == [
                {k: v for k, v in item.items() if k != "request"}
                for item in before[pid]["judgment"]["evaluations"]
            ]
            save(
                phase="restart",
                packet_ids=list(before),
                before_sha256={k: digest(v) for k, v in before.items()},
            )
        async with server(args.config) as second:
            second_status = await call(second, "workspace_status")
            assert first_status["runtime"]["instance_id"] != second_status["runtime"]["instance_id"]
            assert first_status["runtime"]["build_hash"] == second_status["runtime"]["build_hash"]
            assert second_status["runtime"]["code_state"] == "matches_disk"
            for pid, body in before.items():
                assert stored(pid) == body
                inspected = await call(
                    second, "work_inspect", work_id=work["work_id"], view="judgments", packet_id=pid
                )
                assert inspected["items"] == body["judgment"]["evaluations"]
            rows, audit = audit_records(config.db_path)
            assert not any(r["work_id"] == work["work_id"] for r in rows)
            save(
                passed=True,
                phase="finished",
                restarted_runtime=second_status["runtime"],
                audit=audit,
                verification_excluded=True,
                restart_persistence_passed=True,
            )
    except BaseException as exc:
        save(passed=False, failure=repr(exc))
        raise
    print(
        json.dumps({k: v for k, v in report.items() if k != "calls"}, ensure_ascii=False, indent=2)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--work-id")
    parser.add_argument(
        "--verify-request-storage",
        action="store_true",
        help="Create a marked verification work and check real stdio request persistence",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--repeat-after-idle",
        action="store_true",
        help="After idle release, require another demand-started worker and two MCP observations.",
    )
    parser.add_argument(
        "--require-cache-hit",
        action="store_true",
        help="Require a cache hit on the repeated preparation, or the first if not repeating.",
    )
    args = parser.parse_args()
    args.config = args.config.resolve()
    if not args.verify_request_storage and not args.work_id:
        parser.error("--work-id required for lifecycle mode")
    anyio.run(verify_storage if args.verify_request_storage else run, args)


if __name__ == "__main__":
    main()
