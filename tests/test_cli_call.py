import json
import subprocess
import sys
from pathlib import Path

import pytest

from jev_context.cli import read_call_input, write_config
from jev_context.common import uid


@pytest.fixture
def cli_config(tmp_path):
    config = tmp_path / "project.toml"
    root = tmp_path / "project"
    root.mkdir()
    write_config(config, root, tmp_path / "data", ["*.md"])
    text = config.read_text(encoding="utf-8").replace(
        "[engine]", 'contract_version = "2.0"\n[engine]'
    )
    config.write_text(text, encoding="utf-8")
    return config


def command(config, *args, body=None):
    return subprocess.run(
        [sys.executable, "-m", "jev_context", *args, "--config", str(config)],
        input=body,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
    )


def call(config, tool, **args):
    args.setdefault("request_id", uid("req"))
    args.setdefault("contract_version", "2.0")
    return command(config, "call", "--tool", tool, body=json.dumps(args, ensure_ascii=False))


def test_cli_persists_and_restores_same_contract_and_korean_origin(cli_config, tmp_path):
    created = call(
        cli_config,
        "work_open",
        mutation_id=uid("mut"),
        create={
            "title": "계약 복원",
            "goal": "원문을 유지한 실제 호출",
            "scope": {"mode": "investigate", "constraints": ["읽기만 허용"]},
            "origin": {"quote": "기억을 복원해 줘"},
        },
    )
    assert created.returncode == 0, created.stderr
    work = json.loads(created.stdout)["data"]
    request = tmp_path / "input.json"
    request.write_text(
        json.dumps(
            {"contract_version": "2.0", "request_id": uid("req"), "work_id": work["work_id"]}
        ),
        encoding="utf-8",
    )
    restored = command(cli_config, "call", "--tool", "work_open", "--input", str(request))
    assert restored.returncode == 0, restored.stderr
    result = json.loads(restored.stdout)
    assert result["contract_version"] == "2.0"
    assert result["data"]["work_id"] == work["work_id"]
    assert result["data"]["scope"]["constraints"] == ["읽기만 허용"]


@pytest.mark.parametrize(
    "body",
    [
        '{"request_id":"a","request_id":"b"}',
        "[]",
        '"' + "x" * 524288 + '"',
        '{"number":1e999}',
        '{"nested":[{"number":-1e999}]}',
    ],
    ids=["duplicate-key", "array", "oversized", "positive-overflow", "nested-negative-overflow"],
)
def test_cli_invalid_frames_are_rejected_before_mutation(cli_config, body):
    result = command(cli_config, "call", "--tool", "work_open", body=body)
    assert result.returncode == 2
    assert result.stdout == ""
    assert not (cli_config.parent / "data").exists()


def test_cli_finite_exponents_and_korean_input_remain_valid(tmp_path):
    path = tmp_path / "request.json"
    path.write_text('{"근거":[1e308,-1e308,1e-300,42]}', encoding="utf-8")
    assert read_call_input(path) == {"근거": [1e308, -1e308, 1e-300, 42]}


def test_cli_schema_and_domain_failure_exit_code(cli_config):
    schema = command(cli_config, "schema", "--tool", "context_prepare")
    assert schema.returncode == 0
    contract = json.loads(schema.stdout)
    assert contract["contract_version"] == "2.0"
    assert "work_id" in contract["input_schema"]["required"]
    result = call(cli_config, "work_open", work_id="work-missing")
    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome"] == "error"


def test_modal_call_attaches_without_allocating_gpu(cli_config):
    profile = cli_config.parent / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "family": "openjev_modal",
                "model_revision": "pinned",
                "implementation_revision": "pinned",
                "precision": "test",
                "template_revision": "test",
                "sampling": {},
                "score_definition": "test",
                "session_file": str(cli_config.parent / "missing.secret.json"),
            }
        ),
        encoding="utf-8",
    )
    value = cli_config.read_text(encoding="utf-8").replace(
        'state = "disabled"', 'state = "shadow"\nprofile_file = ' + json.dumps(str(profile))
    )
    cli_config.write_text(value, encoding="utf-8")
    result = command(
        cli_config,
        "call",
        "--tool",
        "workspace_status",
        "--prepare-engine",
        body=json.dumps({"request_id": uid("req"), "contract_version": "2.0"}),
    )
    assert result.returncode == 0, result.stderr
    engine = json.loads(result.stdout)["data"]["engine"]
    assert engine["family"] == "openjev_modal"
    assert engine["state"] == "unavailable"
    assert not Path(profile.parent / "missing.secret.json").exists()
