import json
import sys
from dataclasses import replace

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation
from test_desktop_bridge import project

from jev_context import runtime_observation as observation
from jev_context.budget import measure
from jev_context.common import digest
from jev_context.desktop_bridge import DesktopBridge
from jev_context.policy import Config


def test_runtime_build_change_and_unknown_are_never_current(monkeypatch):
    monkeypatch.setattr(observation, "STARTUP_BUILD", "start")
    monkeypatch.setattr(observation, "source_fingerprint", lambda: "start")
    first, second = (
        observation.RuntimeIdentity("mcp_stdio"),
        observation.RuntimeIdentity("mcp_stdio"),
    )
    assert first.identity["instance_id"] != second.identity["instance_id"]
    assert first.snapshot("2.0")["code_state"] == "matches_disk"
    monkeypatch.setattr(observation, "source_fingerprint", lambda: "edited")
    assert first.snapshot("2.0")["code_state"] == "restart_required"
    assert first.snapshot("2.0")["build_hash"] == "start"
    monkeypatch.setattr(observation, "source_fingerprint", lambda: None)
    assert first.snapshot("2.0")["code_state"] == "unknown"


def response(outcome="ok"):
    return {
        "contract_version": "2.0",
        "outcome": outcome,
        "request_id": "secret-query",
        "warnings": [],
        "error": None,
        "data": {
            "packet_id": "packet-test",
            "work_revision": 1,
            "source_set_revision": 2,
            "evidence": [{"text": "절대 저장하면 안 되는 원문"}],
            "scope": {"goal": "private goal"},
        },
    }


def observe(config, path, outcome="ok", **kwargs):
    observation.observe_response(
        config,
        kwargs.get("name", "context_prepare"),
        {"work_id": "work-test", "query": "비공개 사용자 질문"},
        response(outcome),
        observation.RuntimeIdentity("mcp_stdio").snapshot("2.0"),
        kwargs.get("client_name", "codex-private-user"),
        digest(path.read_bytes()),
    )


@pytest.mark.parametrize("outcome", ["ok", "partial", "insufficient"])
def test_receipt_has_no_content_or_claim_of_host_use(tmp_path, outcome):
    path, _ = project(tmp_path)
    config = Config.load(path)
    observe(config, path, outcome)
    data = observation.observation_status(config, path)
    receipt = data["last_context"]
    assert receipt["outcome"] == outcome
    assert receipt["wire_bytes"] == measure(response(outcome))
    assert receipt["token_count"] is None
    assert receipt["host_receipt"] == receipt["answer_use"] == "not_observed"
    assert receipt["client_kind"] == "codex"
    raw = observation._path(config).read_text("utf-8")
    for secret in [
        "secret-query",
        "절대 저장하면",
        "private goal",
        "비공개",
        "private-user",
        str(tmp_path),
    ]:
        assert secret not in raw
    assert observation.observation_status(replace(config, project_id="project-another"), path) == {
        "state": "project_mismatch"
    }
    path.write_text(path.read_text("utf-8") + "\n# settings changed\n", encoding="utf-8")
    assert observation.observation_status(config, path) == {"state": "settings_changed"}


def test_probe_readonly_errors_and_unrelated_calls_do_not_record(tmp_path):
    path, _ = project(tmp_path)
    config = Config.load(path)
    observe(config, path, client_name="jev-context-probe")
    observe(replace(config, read_only=True), path)
    observe(config, path, "error")
    observe(config, path, name="work_open")
    assert not observation._path(config).exists()


def test_broken_sidecar_and_write_failure_are_optional(tmp_path, monkeypatch):
    path, _ = project(tmp_path)
    config = Config.load(path)
    observe(config, path)
    target = observation._path(config)
    for content in ["broken", "[]", '{"schema":1,"project_id":"x"}', "x" * 33000]:
        target.write_text(content, encoding="utf-8")
        assert observation.observation_status(config, path)["state"] in {
            "unavailable",
            "project_mismatch",
        }
    monkeypatch.setattr(observation, "atomic_write", lambda *args: (_ for _ in ()).throw(OSError()))
    observe(config, path)  # No exception replaces the original response.


def test_readonly_reads_and_bridge_refresh_preserve_observation(tmp_path):
    path, _ = project(tmp_path)
    config = Config.load(path)
    observe(config, path)
    before = observation._path(config).read_bytes()
    bridge = DesktopBridge(path)
    try:
        snapshot = bridge.overview()
        assert snapshot["connection"]["runtime"]["transport"] == "desktop_bridge"
        assert (
            snapshot["connection"]["host_observation"]["last_context"]["packet_id"] == "packet-test"
        )
        assert snapshot["connection"]["desktop_current_session"] == "not_observed"
        assert observation._path(config).read_bytes() == before
    finally:
        bridge.close()


def test_setup_lock_does_not_block_observation_and_observation_lock_preserves_record(tmp_path):
    from jev_context.onboarding import locked
    from jev_context.project_files import safe_path
    from jev_context.storage import FileLock

    path, _ = project(tmp_path)
    config = Config.load(path)
    with locked(config.project_root):
        observe(config, path)
        assert observation.observation_status(config, path)["state"] == "recorded"
    before = observation._path(config).read_bytes()
    with FileLock(safe_path(config.project_root, ".local/jev-runtime/observation.lock")):
        observe(config, path, outcome="partial")
    assert observation._path(config).read_bytes() == before
    observe(config, path, outcome="partial")
    assert observation.observation_status(config, path)["last_context"]["outcome"] == "partial"


def test_real_mcp_response_observation_restart_and_health_probe(tmp_path):
    path, wid = project(tmp_path)
    config = Config.load(path)

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "jev_context", "serve", "--config", str(path)]
        )
        instances = []
        for index in range(2):
            async with (
                stdio_client(params) as streams,
                ClientSession(
                    *streams, client_info=Implementation(name="codex-test", version="1")
                ) as client,
            ):
                await client.initialize()
                status = await client.call_tool(
                    "workspace_status", {"contract_version": "2.0", "request_id": f"status-{index}"}
                )
                runtime = status.structuredContent["data"]["runtime"]
                assert runtime["code_state"] == "matches_disk"
                assert runtime["contract_version"] == "2.0"
                instances.append(runtime["instance_id"])
                result = await client.call_tool(
                    "context_prepare",
                    {
                        "contract_version": "2.0",
                        "request_id": f"context-{index}",
                        "work_id": wid,
                        "query": "기존 기록",
                        "judge_mode": "off",
                        "budget_bytes": 18000,
                    },
                )
                assert json.loads(result.content[0].text) == result.structuredContent
                receipt = observation.observation_status(config, path)["last_context"]
                assert receipt["packet_id"] == result.structuredContent["data"]["packet_id"]
                assert receipt["runtime"]["instance_id"] == instances[-1]
                assert receipt["wire_bytes"] == measure(result.structuredContent)
        assert instances[0] != instances[1]

    anyio.run(run)
    before = observation._path(config).read_bytes()
    bridge = DesktopBridge(path)
    try:
        snapshot = bridge.connection_check()
        assert snapshot["connection"]["mcp_stdio"] == "verified"
        assert snapshot["connection"]["probe_runtime"]["transport"] == "mcp_stdio"
        assert observation._path(config).read_bytes() == before
    finally:
        bridge.close()
