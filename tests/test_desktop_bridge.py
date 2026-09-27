import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jev_context import desktop_bridge
from jev_context.common import DomainError, uid
from jev_context.policy import Config
from jev_context.service import Service


def project(tmp_path, *, enabled=False, initialize=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "project"
    root.mkdir()
    path = tmp_path / "project.toml"
    path.write_text(
        'project_root = "project"\ndata_root = "state"\n'
        f'project_id = "{uid("project")}"\ncontract_version = "2.0"\n'
        'allowed_paths = ["*.md"]\n[engine]\n'
        f'state = "{"shadow" if enabled else "disabled"}"\nprofile_file = "profile.json"\n',
        encoding="utf-8",
    )
    (tmp_path / "profile.json").write_text(
        json.dumps({"family": "semif_openvino", "model_revision": "fake", "device": "GPU"}),
        encoding="utf-8",
    )
    work_id = None
    if initialize:
        service = Service(Config.load(path))
        try:
            result = service.call(
                "work_open",
                {
                    "request_id": uid("req"),
                    "mutation_id": uid("mut"),
                    "contract_version": "2.0",
                    "create": {
                        "title": "기존 작업",
                        "goal": "기존 기록 보존",
                        "scope": {"mode": "design", "constraints": ["설계만"]},
                        "origin": {"quote": "기존 기록 보존"},
                    },
                },
            )
            assert result["outcome"] == "ok", result
            work_id = result["data"]["work_id"]
        finally:
            service.close()
    return path, work_id


class FakeEngine:
    def __init__(self):
        self.profile = {"family": "semif_openvino"}
        self.fingerprint = "fake-profile"
        self.current = "idle"
        self.active = self.preparations = self.closes = 0
        self.controls = []

    @property
    def state(self):
        return self.current

    def snapshot(self):
        return {
            "state": self.current,
            "broker_pid": 123 if self.current != "idle" else None,
            "worker_pid": 456 if self.current == "shadow" else None,
            "active_requests": self.active,
            "idle_timeout_seconds": 120,
        }

    def prepare(self):
        self.preparations += 1
        self.current = "preparing"

    def _call(self, action):
        assert action == "stop"
        self.controls.append(action)
        if self.active or self.current == "preparing":
            raise DomainError("engine_busy", "Active work", True)
        self.current = "idle"
        return {"stopping": True}

    def close(self):
        self.closes += 1


def call(bridge, method, **params):
    return bridge.dispatch({"id": 1, "method": method, "params": params})


def test_overview_work_read_and_close_never_prepare_or_stop_model(tmp_path, monkeypatch):
    config, work_id = project(tmp_path, enabled=True)
    engine = FakeEngine()
    monkeypatch.setattr(desktop_bridge, "prepare_engine", lambda config: engine)
    bridge = desktop_bridge.DesktopBridge(config)
    first = call(bridge, "overview")["result"]
    assert first["works"]["items"][0]["work_id"] == work_id
    assert first["engine"]["state"] == "idle"
    engine.current = "shadow"
    second = call(bridge, "overview")["result"]
    assert second["engine"]["state"] == "shadow"  # Fresh process state, not captured at startup.
    detail = call(bridge, "work_open", work_id=work_id)["result"]
    assert detail["goal"] == "기존 기록 보존"
    assert detail["scope"]["constraints"] == ["설계만"] and detail["revision"] == 1
    bridge.close()
    assert engine.preparations == 0 and engine.controls == [] and engine.closes == 1
    assert engine.current == "shadow"  # Closing a manager connection preserves another client.


def test_explicit_prepare_and_authenticated_stop_respect_active_work(tmp_path, monkeypatch):
    config, _ = project(tmp_path, enabled=True)
    engine = FakeEngine()
    monkeypatch.setattr(desktop_bridge, "prepare_engine", lambda config: engine)
    bridge = desktop_bridge.DesktopBridge(config)
    result = call(bridge, "model_prepare")
    assert result["result"]["engine"]["state"] == "preparing" and engine.preparations == 1
    assert call(bridge, "model_stop")["error"]["code"] == "engine_busy"
    engine.current, engine.active = "shadow", 1
    assert call(bridge, "model_stop")["error"]["code"] == "engine_busy"
    engine.active = 0
    assert call(bridge, "model_stop")["result"]["engine"]["state"] == "idle"
    assert engine.controls == ["stop", "stop", "stop"]


def test_missing_profile_keeps_records_and_missing_database_is_not_created(tmp_path):
    config, work_id = project(tmp_path / "existing", enabled=True)
    config.with_name("profile.json").unlink()
    bridge = desktop_bridge.DesktopBridge(config)
    overview = call(bridge, "overview")["result"]
    assert overview["engine"]["profile_error"]["code"] == "invalid_profile"
    assert overview["works"]["items"][0]["work_id"] == work_id
    assert call(bridge, "model_prepare")["error"]["code"] == "engine_unavailable"
    empty, _ = project(tmp_path / "empty", initialize=False)
    overview = call(bridge, "overview", config_path=str(empty))["result"]
    assert overview["workspace_error"]["code"] == "not_initialized"
    assert not Config.load(empty).db_path.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 1, "method": "invoke", "params": {"command": "anything"}},
        {"id": 1, "method": "work_open", "params": {"create": {}}},
        {"id": 1, "method": "model_stop", "params": {"pid": 1}},
        {"id": float("inf"), "method": "overview", "params": {}},
    ],
)
def test_protocol_rejects_arbitrary_commands_mutations_and_invalid_ids(tmp_path, payload):
    config, _ = project(tmp_path)
    response = desktop_bridge.DesktopBridge(config).dispatch(payload)
    assert response["error"]["code"] == "invalid_argument"
    json.dumps(response, allow_nan=False)


@pytest.mark.parametrize("enabled", [False, True])
def test_official_stdio_check_is_verified_without_claiming_native_host_connection(
    tmp_path, monkeypatch, enabled
):
    config, _ = project(tmp_path, enabled=enabled)
    engine = FakeEngine()
    monkeypatch.setattr(desktop_bridge, "prepare_engine", lambda config: engine)
    bridge = desktop_bridge.DesktopBridge(config)
    result = call(bridge, "connection_check")["result"]
    assert result["connection"]["mcp_stdio"] == "verified", result["connection"]
    assert result["connection"]["wire_verified"] is True
    assert len(result["connection"]["tools"]) == 13
    assert result["connection"]["desktop_current_session"] == "not_observed"
    assert result["engine"]["state"] == ("idle" if enabled else "disabled")
    assert engine.preparations == 0


def test_json_lines_process_survives_bad_input_and_reads_existing_work(tmp_path):
    config, work_id = project(tmp_path)
    payload = (
        "bad json\n"
        + json.dumps({"id": "read", "method": "work_open", "params": {"work_id": work_id}})
        + "\n"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "jev_context.desktop_bridge", "--config", str(config)],
        input=payload,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert completed.returncode == 0, completed.stderr
    malformed, restored = map(json.loads, completed.stdout.splitlines())
    assert malformed["id"] is None and malformed["error"]["code"] == "invalid_argument"
    assert restored["id"] == "read" and restored["result"]["work_id"] == work_id
