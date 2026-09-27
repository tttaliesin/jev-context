import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

from jev_context import onboarding as setup
from jev_context.common import DomainError
from jev_context.desktop_bridge import DesktopBridge
from jev_context.policy import Config
from jev_context.service import Service


def project(tmp_path):
    root = tmp_path / "한글 프로젝트"
    root.mkdir()
    return root, Path(setup.create_project(root, ["README.md", "docs/*.md"])["config_path"])


def install(path):
    return setup.install(path, setup.preview(path)[0]["fingerprint"])


def test_create_reopen_and_no_automatic_collection(tmp_path):
    root, path = project(tmp_path)
    original = path.read_bytes()
    (root / "README.md").write_text("not automatically collected")
    result = setup.create_project(root, ["other/*.py"])
    assert path.read_bytes() == original
    assert result["model_mode"] == "disabled"
    with_db = Service(Config.load(path))
    try:
        data = with_db.call("workspace_status", {"contract_version": "2.0", "request_id": "read"})[
            "data"
        ]
        assert data["sources"] == 0
        assert data["items"] == []
    finally:
        with_db.close()


@pytest.mark.parametrize("allowed", [["../*.md"], ["C:/work"], ["/etc"], [False], "*.md"])
def test_disallow_escaped_scope(tmp_path, allowed):
    with pytest.raises(DomainError, match="상대 경로"):
        setup.create_project(tmp_path, allowed)
    assert not (tmp_path / ".local/project.toml").exists()


def test_install_preserve_unrelated_settings_and_restore_exact_bytes(tmp_path):
    root, path = project(tmp_path)
    settings = root / setup.TARGETS[0]
    settings.parent.mkdir()
    original = (
        b'# user comment\r\nmodel = "existing"\r\n[mcp_servers.other]\r\ncommand = "other"\r\n'
    )
    settings.write_bytes(original)
    skill = root / setup.TARGETS[1]
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"prior skill")
    assert install(path)["installed"]
    parsed = tomllib.loads(settings.read_text())
    assert parsed["mcp_servers"]["other"] == {"command": "other"}
    assert parsed["model"] == "existing"
    assert parsed["mcp_servers"]["jev_context"]["cwd"] == str(root)
    first = setup.journal(root)
    assert install(path)["installed"]
    assert setup.journal(root) == first
    assert setup.restore(path)["installed"] is False
    assert settings.read_bytes() == original
    assert skill.read_bytes() == b"prior skill"
    assert Config.load(path).db_path.is_file()


def test_update_existing_entry_preserves_sibling_and_handles_env(tmp_path):
    root, path = project(tmp_path)
    settings = root / setup.TARGETS[0]
    settings.parent.mkdir()
    settings.write_text(
        '[mcp_servers.jev_context]\ncommand = "old"\n'
        '[mcp_servers.jev_context.env]\nX = "x"\n'
        '[mcp_servers.other]\ncommand = "keep"\n'
    )
    install(path)
    parsed = tomllib.loads(settings.read_text())
    assert parsed["mcp_servers"]["jev_context"]["command"] == sys.executable
    assert parsed["mcp_servers"]["other"]["command"] == "keep"


def test_stale_preview_and_modified_restore_are_rejected(tmp_path):
    root, path = project(tmp_path)
    plan = setup.preview(path)[0]
    settings = root / setup.TARGETS[0]
    settings.parent.mkdir()
    settings.write_text('model = "new"\n')
    with pytest.raises(DomainError, match="다시 보기"):
        setup.install(path, plan["fingerprint"])
    install(path)
    settings.write_text(settings.read_text() + "\n# edited after install\n")
    before = settings.read_bytes()
    with pytest.raises(DomainError, match="수정되어"):
        setup.restore(path)
    assert settings.read_bytes() == before


def test_partial_install_rolls_back_and_keeps_original(tmp_path, monkeypatch):
    root, path = project(tmp_path)
    original = setup.atomic_write

    def broken(target, content):
        if target == root / setup.TARGETS[1]:
            raise OSError("simulated disk error")
        return original(target, content)

    monkeypatch.setattr(setup, "atomic_write", broken)
    with pytest.raises(OSError, match="disk error"):
        install(path)
    assert not (root / setup.TARGETS[0]).exists()
    assert setup.journal(root)["status"] == "rolled_back"


def test_interrupted_install_can_restore_and_invalid_toml_is_readable(tmp_path):
    root, path = project(tmp_path)
    install(path)
    backup = setup.journal(root)
    backup["status"] = "prepared"
    setup.write_journal(root, backup)
    assert setup.status(path)["recovery_pending"]
    with pytest.raises(DomainError, match="중단된 설치"):
        install(path)
    setup.restore(path)
    assert not (root / setup.TARGETS[0]).exists()
    (root / setup.TARGETS[0]).write_text("[invalid")
    assert setup.status(path)["configuration_error"]


def test_cli_and_transport_health_check_do_not_confirm_codex(tmp_path):
    _, path = project(tmp_path)
    install(path)
    pending = setup.challenge(path)["confirmation"]
    bridge = DesktopBridge(path)
    try:
        assert bridge.connection_check()["connection"]["mcp_stdio"] == "verified"
        bridge.service_call("workspace_status")
    finally:
        bridge.close()
    assert setup.status(path)["confirmation"]["state"] == "waiting"
    config = Config.load(path)
    args = {"request_id": pending["request_id"]}
    setup.observe_confirmation(
        config, "workspace_status", args, {"outcome": "ok"}, "jev-manager", path
    )
    assert setup.status(path)["confirmation"]["state"] == "waiting"


def test_confirmation_wrong_token_expiry_and_changed_configuration(tmp_path):
    _, path = project(tmp_path)
    install(path)
    setup.challenge(path)
    config = Config.load(path)
    setup.observe_confirmation(
        config,
        "workspace_status",
        {"request_id": "jev-connect-wrong"},
        {"outcome": "ok"},
        "codex-test-client",
        path,
    )
    assert setup.status(path)["confirmation"]["state"] == "waiting"
    receipt = setup.confirmation_path(config)
    item = json.loads(receipt.read_text())
    item["expires_at"] = 0
    receipt.write_text(json.dumps(item))
    setup.observe_confirmation(
        config,
        "workspace_status",
        {"request_id": item["request_id"]},
        {"outcome": "ok"},
        "codex-test-client",
        path,
    )
    assert setup.status(path)["confirmation"]["state"] == "expired"
    setup.challenge(path)
    path.write_text(path.read_text() + "\n# configuration changed\n")
    assert setup.status(path)["confirmation"]["state"] == "settings_changed"


def test_real_mcp_transport_records_challenge_from_self_reported_client(tmp_path):
    _, path = project(tmp_path)
    install(path)
    pending = setup.challenge(path)["confirmation"]

    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "jev_context", "serve", "--config", str(path)],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        )
        async with (
            stdio_client(parameters) as streams,
            ClientSession(
                *streams, client_info=Implementation(name="codex-test-client", version="test")
            ) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "workspace_status", {"contract_version": "2.0", "request_id": pending["request_id"]}
            )
            assert result.structuredContent["outcome"] == "ok"

    anyio.run(scenario)
    observed = setup.status(path)["confirmation"]
    assert observed["state"] == "received"
    assert observed["client_name"] == "codex-test-client"
    setup.restore(path)
    assert setup.status(path)["confirmation"]["state"] == "not_requested"


def test_read_only_and_symlink_targets_are_not_overwritten(tmp_path):
    root, path = project(tmp_path)
    path.write_text("read_only = true\n" + path.read_text())
    with pytest.raises(DomainError, match="읽기 전용"):
        setup.preview(path)
    path.write_text(path.read_text().replace("read_only = true\n", ""))
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / ".codex").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink permission unavailable")
    with pytest.raises(DomainError, match="프로젝트 밖"):
        setup.preview(path)
    assert list(outside.iterdir()) == []


def test_existing_custom_config_and_reloaded_server_required(tmp_path):
    root, path = project(tmp_path)
    custom = root / "custom.toml"
    path.rename(custom)
    assert setup.initialize_existing(custom)["project_ready"]
    assert not path.exists()
    install(custom)
    old_hash = setup.stamp(custom.read_bytes())
    custom.write_text(custom.read_text() + "\n# newer configuration\n")
    pending = setup.challenge(custom)["confirmation"]
    config = Config.load(custom)
    setup.observe_confirmation(
        config,
        "workspace_status",
        {"request_id": pending["request_id"]},
        {"outcome": "ok"},
        "codex-test-client",
        custom,
        old_hash,
    )
    assert setup.status(custom)["confirmation"]["state"] == "waiting"


def test_install_lock_and_malformed_toml_do_not_write(tmp_path):
    root, path = project(tmp_path)
    with setup.locked(root), pytest.raises(DomainError, match="진행 중"):
        install(path)
    settings = root / setup.TARGETS[0]
    settings.parent.mkdir()
    settings.write_text('mcp_servers = "wrong"\n')
    with pytest.raises(DomainError, match="테이블"):
        setup.preview(path)
    assert setup.status(path)["installed"] is False


def test_selected_folder_binding_rejects_configuration_for_another_project(tmp_path):
    _, path = project(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "jev_context.onboarding"],
        input=json.dumps(
            {"action": "initialize", "config_path": str(path), "expected_root": str(other)}
        ),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "project_mismatch"
    assert list(other.iterdir()) == []
