"""Real MCP transports share a fake model through an independently owned broker."""

import json
import os
import secrets
import socket
import struct
import sys
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_engines import request, semif_fixture
from test_shared_engine import Runtime, process_alive

from jev_context.common import DomainError, uid
from jev_context.shared_engine import MAX_FRAME, PROTOCOL, frame_read, frame_write


@pytest.fixture
def shared_mcp_runtime(tmp_path, monkeypatch):
    directory = tmp_path / "rt"
    directory.mkdir()
    fake_config = semif_fixture(directory, monkeypatch)
    source_root = Path(__file__).resolve().parents[1] / "src"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(directory / "fakes"), str(source_root))))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("FAKE_COMPILE_SECONDS", "1.3")
    profile = json.loads(Path(fake_config.engine["profile_file"]).read_text(encoding="utf-8"))
    profile.update(
        idle_timeout_seconds=4,
        prepare_timeout_seconds=5,
        omit_shadow_purposes=["presentation"],
    )
    profile_file = directory / "profile.json"
    profile_file.write_text(json.dumps(profile), encoding="utf-8")
    project = tmp_path / "p"
    project.mkdir()
    (project / "rules.md").write_text(
        "공유 모델 근거: 현재 프로젝트 원문을 보존한다.\n", encoding="utf-8"
    )
    config = tmp_path / "project.toml"
    config.write_text(
        "\n".join(
            [
                f"project_root = {json.dumps(str(project))}",
                f"data_root = {json.dumps(str(tmp_path / 'd'))}",
                'project_id = "project-shared-mcp"',
                'allowed_paths = ["*.md"]',
                'contract_version = "2.0"',
                "timeout_seconds = 4",
                "judgment_seconds = 2",
                "[engine]",
                'state = "shadow"',
                f"profile_file = {json.dumps(str(profile_file))}",
            ]
        ),
        encoding="utf-8",
    )
    runtime = Runtime(profile)
    controller = runtime.proxy()
    try:
        # The SDK owns each MCP child through a Windows Job. Start the lightweight
        # broker from the test parent, so neither MCP transport owns its lifetime.
        controller._start(persistent=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = runtime.snapshot(controller)
            if snapshot["broker_pid"]:
                break
            time.sleep(0.04)
        else:
            pytest.fail("Independent fake-model broker did not start")
        assert snapshot["state"] == "idle" and snapshot["worker_pid"] is None
        yield runtime, controller, config
    finally:
        # An authenticated stop correctly refuses preparation/inference in progress.
        deadline = time.monotonic() + profile["prepare_timeout_seconds"] + 6
        while time.monotonic() < deadline:
            snapshot = runtime.snapshot(controller)
            runtime.collect_logged_workers()
            if not snapshot["broker_pid"]:
                break
            if snapshot["state"] != "preparing" and not snapshot.get("active_requests"):
                try:
                    controller._call("stop", timeout=1)
                    break
                except DomainError as exc:
                    if exc.code != "engine_busy":
                        raise
            time.sleep(0.05)
        deadline = time.monotonic() + 5
        while any(process_alive(pid) for pid in runtime.pids) and time.monotonic() < deadline:
            runtime.collect_logged_workers()
            time.sleep(0.05)
        for proxy in runtime.proxies:
            proxy.close()
        assert not any(process_alive(pid) for pid in runtime.pids), "Test-owned runtime leaked"


@asynccontextmanager
async def mcp_server(config):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "jev_context", "serve", "--config", str(config), "--prepare-engine"],
        env=dict(os.environ),
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=8)) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "jev-context"
            assert len((await session.list_tools()).tools) == 10
            yield session


async def call(session, tool, **arguments):
    arguments.update(request_id=uid("req"), contract_version="2.0")
    if tool == "source_sync" or "create" in arguments:
        arguments["mutation_id"] = uid("mut")
    result = await session.call_tool(tool, arguments)
    assert not result.isError, result.structuredContent
    assert json.loads(result.content[0].text) == result.structuredContent
    return result.structuredContent


def test_two_real_mcp_hosts_share_demand_started_model_and_status_does_not_keep_it_alive(
    shared_mcp_runtime,
):
    runtime, controller, config = shared_mcp_runtime
    original = runtime.snapshot(controller)

    def assert_unloaded():
        snapshot = runtime.snapshot(controller)
        assert snapshot["state"] == "idle" and snapshot["worker_pid"] is None
        assert snapshot["instance_id"] == original["instance_id"]
        assert process_alive(snapshot["broker_pid"])

    async def scenario():
        async with mcp_server(config) as survivor:
            assert_unloaded()
            async with mcp_server(config) as first:
                assert_unloaded()
                for session in (first, survivor):
                    assert (await call(session, "workspace_status"))["data"]["engine"][
                        "state"
                    ] == "idle"
                    assert_unloaded()
                created = await call(
                    first,
                    "work_open",
                    create={
                        "title": "공유 모델 시험",
                        "goal": "공유 모델 근거 확인",
                        "scope": {"mode": "investigate", "constraints": ["원문 보존"]},
                        "origin": {"quote": "공유 모델 근거 확인"},
                    },
                )
                work_id = created["data"]["work_id"]
                restored = await call(survivor, "work_open", work_id=work_id)
                assert restored["data"]["revision"] == created["data"]["revision"]
                assert_unloaded()
                for session in (first, survivor):
                    synced = await call(
                        session,
                        "source_sync",
                        items=[{"kind": "file", "relative_path": "rules.md"}],
                    )
                    assert synced["outcome"] == "ok"
                    assert_unloaded()
                source_id = synced["data"]["items"][0]["source_id"]
                context = {
                    "work_id": work_id,
                    "query": "공유 모델 근거",
                    "source_ids": [source_id],
                    "budget_bytes": 32768,
                    "language": "ko",
                }
                for session in (first, survivor):
                    off = await call(session, "context_prepare", **context, judge_mode="off")
                    assert off["data"]["judgment"]["status"] == "skipped"
                    assert off["data"]["evidence"]
                    assert_unloaded()

                started = time.monotonic()
                warming = await call(first, "context_prepare", **context, judge_mode="required")
                assert time.monotonic() - started < 2
                assert warming["outcome"] == "insufficient"
                judgments = (
                    await call(
                        first,
                        "work_inspect",
                        work_id=work_id,
                        view="judgments",
                        packet_id=warming["data"]["packet_id"],
                    )
                )["data"]["items"]
                assert judgments and all(
                    row.get("reason") == "engine_preparing" for row in judgments
                )
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    status = (await call(survivor, "workspace_status"))["data"]["engine"]
                    if status["state"] == "shadow":
                        break
                    assert status["state"] == "preparing", status
                    await anyio.sleep(0.05)
                else:
                    pytest.fail("The first real MCP judgment did not prepare the fake model")
                prepared = runtime.snapshot(controller)
                assert prepared["instance_id"] == original["instance_id"]
                assert process_alive(prepared["worker_pid"])
                for session in (first, survivor):
                    answer = await call(
                        session, "context_prepare", **context, judge_mode="required"
                    )
                    assert answer["data"]["judgment"]["status"] == "observed"
                    assert runtime.snapshot(controller)["worker_pid"] == prepared["worker_pid"]

            # Closing the MCP that initiated preparation must not kill a shared worker.
            answer = await call(survivor, "context_prepare", **context, judge_mode="required")
            assert answer["data"]["judgment"]["status"] == "observed"
            after_close = runtime.snapshot(controller)
            for key in ("instance_id", "broker_pid", "worker_pid"):
                assert after_close[key] == prepared[key]

            deadline = time.monotonic() + runtime.profile["idle_timeout_seconds"] + 6
            while time.monotonic() < deadline:
                await call(survivor, "workspace_status")
                snapshot = runtime.snapshot(controller)
                if snapshot["state"] == "idle" and snapshot["worker_pid"] is None:
                    break
                await anyio.sleep(0.08)
            else:
                pytest.fail("MCP status polling prevented idle worker release")
            assert not process_alive(prepared["worker_pid"])
            for _ in range(3):
                await call(survivor, "workspace_status")
                assert_unloaded()
                await anyio.sleep(0.05)
        assert_unloaded()

    anyio.run(scenario)


def test_broker_refuses_bad_authentication_and_malformed_frames_without_starting_model(
    shared_mcp_runtime,
):
    runtime, controller, _ = shared_mcp_runtime
    original = runtime.snapshot(controller)
    endpoint = json.loads(controller.endpoint.read_text(encoding="utf-8"))

    def refused(connection):
        connection.settimeout(2)
        try:
            assert connection.recv(1) == b""
        except ConnectionResetError:
            pass

    for fault in ("oversized", "duplicate-key", "wrong-proof"):
        with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=2) as connection:
            if fault == "oversized":
                connection.sendall(struct.pack("!I", MAX_FRAME + 1))
            elif fault == "duplicate-key":
                raw = b'{"protocol":1,"protocol":1}'
                connection.sendall(struct.pack("!I", len(raw)) + raw)
            else:
                deadline = time.monotonic() + 2
                frame_write(
                    connection,
                    {
                        "protocol": PROTOCOL,
                        "nonce": secrets.token_hex(32),
                        "identity": controller.identity,
                    },
                    deadline,
                )
                assert frame_read(connection, deadline)["proof"]
                frame_write(
                    connection,
                    {
                        "proof": "incorrect-client-proof",
                        "request_id": secrets.token_hex(16),
                        "action": "evaluate",
                        "request": request(controller),
                    },
                    deadline,
                )
            refused(connection)
        snapshot = runtime.snapshot(controller)
        assert snapshot["state"] == "idle" and snapshot["worker_pid"] is None
        assert snapshot["instance_id"] == original["instance_id"]
        assert process_alive(snapshot["broker_pid"])
