"""Exercise an independently started, real local broker through two MCP transports."""

import argparse
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jev_context.common import uid
from jev_context.policy import Config
from jev_context.shared_engine import SharedLocalEngine


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--work-id", required=True)
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
    anyio.run(run, args)


if __name__ == "__main__":
    main()
