"""Safeguards for the isolated, revision-frozen resumption comparison runner."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/resume_benchmark.py"
SPEC = importlib.util.spec_from_file_location("resume_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_rollout_is_bound_to_returned_thread_and_workspace(tmp_path):
    trial = tmp_path / "trial"
    trial.mkdir()
    rollout = tmp_path / "rollout.jsonl"
    thread = "00000000-1111-2222-3333-444444444444"
    events = [
        {"type": "session_meta", "payload": {"id": thread, "cwd": str(trial)}},
        {"type": "turn_context", "payload": {"model": "actual-model", "effort": "ultra"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 10,
                        "total_tokens": 110,
                    }
                },
            },
        },
    ]
    rollout.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    result = benchmark.parse_rollout(rollout, thread, trial)
    assert result["measurement_complete"]
    assert result["uncached_input_tokens"] == 60
    assert result["observed_models"] == ["actual-model"]
    assert result["observed_reasoning_efforts"] == ["ultra"]
    assert result["tool_call_count"] == 1
    assert result["rollout_sha256"] == benchmark.sha(rollout)
    with pytest.raises(RuntimeError, match="session ID"):
        benchmark.parse_rollout(rollout, "wrong-id", trial)
    with pytest.raises(RuntimeError, match="workspace"):
        benchmark.parse_rollout(rollout, thread, tmp_path / "elsewhere")


def test_missing_effort_does_not_invent_default(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "session_meta", "payload": {"id": "thread", "cwd": str(tmp_path)}},
                {"type": "turn_context", "payload": {"model": "model"}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 5,
                                "cached_input_tokens": 0,
                                "output_tokens": 1,
                                "total_tokens": 6,
                            }
                        },
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    result = benchmark.parse_rollout(rollout, "thread", tmp_path)
    assert not result["measurement_complete"]
    assert result["observed_reasoning_efforts"] == []


def test_no_other_session_is_read_to_guess_a_missing_rollout(tmp_path, monkeypatch):
    other = tmp_path / "rollout-other.jsonl"
    other.write_text("PRIVATE OTHER SESSION", encoding="utf-8")
    original = Path.read_text

    def guarded(path, *args, **kwargs):
        assert path != other, "Must not read another session"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    result = benchmark.observe_rollout("00000000-1111-2222-3333-444444444444", tmp_path, tmp_path)
    assert not result["measurement_complete"]
    assert "found 0" in result["rollout_error"]


def test_descriptor_cannot_escape_repository(tmp_path):
    (tmp_path / "valid.py").write_text("pass", encoding="utf-8")
    assert benchmark.relative_file("valid.py", tmp_path) == tmp_path / "valid.py"
    with pytest.raises(ValueError):
        benchmark.relative_file("../elsewhere.py", tmp_path)
    with pytest.raises(ValueError):
        benchmark.relative_file(str(tmp_path / "valid.py"), tmp_path)


def test_only_existing_mcp_definitions_are_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[mcp_servers.node_repl]\ncommand = "node"\n', encoding="utf-8"
    )
    assert benchmark.configured_disabled_mcp() == ("node_repl",)


def test_only_new_exact_trial_trust_entries_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text('model = "same"\n', encoding="utf-8")
    trial = tmp_path / "experiment/t/01/w"
    permitted = benchmark.automatic_trust_paths([trial])
    before = benchmark.user_config_observation(permitted)
    config.write_text(
        'model = "same"\n[projects.' + json.dumps(str(trial)) + ']\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    after = benchmark.user_config_observation(permitted)
    assert before["sha256"] != after["sha256"]
    assert before["effective_sha256"] == after["effective_sha256"]
    assert benchmark.automatic_trust_paths([trial]) == []


@pytest.mark.parametrize(
    "change", ["model", "effort", "provider", "hooks", "other-project", "extra-key"]
)
def test_meaningful_config_changes_still_change_effective_hash(tmp_path, monkeypatch, change):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    base = 'model = "same"\nmodel_reasoning_effort = "ultra"\nmodel_provider = "openai"\n'
    config.write_text(base, encoding="utf-8")
    trial = tmp_path / "experiment/t/01/w"
    before = benchmark.user_config_observation([trial])
    if change == "model":
        updated = base.replace('"same"', '"different"')
    elif change == "effort":
        updated = base.replace('"ultra"', '"high"')
    elif change == "provider":
        updated = base.replace('"openai"', '"other"')
    elif change == "hooks":
        updated = base + "[features]\nhooks = true\n"
    else:
        target = trial if change == "extra-key" else tmp_path / "other/t/01/w"
        updated = base + "[projects." + json.dumps(str(target)) + ']\ntrust_level = "trusted"\n'
        if change == "extra-key":
            updated += "other = true\n"
    config.write_text(updated, encoding="utf-8")
    assert (
        benchmark.user_config_observation([trial])["effective_sha256"] != before["effective_sha256"]
    )


def test_baseline_distinguishes_assertions_from_collection_errors(tmp_path):
    xml = tmp_path / "tests.xml"
    xml.write_text(
        '<testsuites><testsuite><testcase><failure message="assert False">'
        'AssertionError</failure></testcase><testcase><error message="import error"/>'
        "</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    result = benchmark.parse_junit(xml)
    assert result["failures"] == result["assertion_failures"] == 1
    assert result["errors"] == 1


@pytest.mark.parametrize("separate_source_root", [False, True])
def test_prepare_freezes_identical_sources_and_latin_orders(
    tmp_path, monkeypatch, separate_source_root
):
    root = tmp_path / "repo"
    source = root / "src/jev_context"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("# unchanged real source\n", encoding="utf-8")
    (source / "context.py").write_text("def original(): return False\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/conftest.py").write_text("", encoding="utf-8")
    contexts = tmp_path / "contexts"
    contexts.mkdir()
    for condition in ("e1", "e2"):
        (contexts / f"{condition}.json").write_text(
            json.dumps({"condition": condition}), encoding="utf-8"
        )
    benchmark.write_json(contexts / "generation.json", {"e1": {"elapsed_seconds": 1}})
    hidden = tmp_path / "hidden.py"
    hidden.write_text("def test_behavior(): assert False\n", encoding="utf-8")
    resource = tmp_path / "policy.md"
    resource.write_text("모든 조건에 같은 원문", encoding="utf-8")
    case = tmp_path / "case.json"
    benchmark.write_json(
        case,
        {
            "allowed_files": ["src/jev_context/context.py"],
            "public_tests": [],
            "hidden_test": str(hidden),
            "prompt": "고쳐라",
            "resources": {"sources/policy.md": str(resource)},
        },
    )
    live_root = tmp_path / "live-repo" if separate_source_root else root
    live_root.mkdir(exist_ok=True)
    monkeypatch.setattr(benchmark, "ROOT", live_root)
    monkeypatch.setattr(
        benchmark,
        "grade",
        lambda *args, **kwargs: (
            {
                "passed": False,
                "exit_code": 1,
                "failures": 1,
                "assertion_failures": 1,
                "errors": 0,
                "skipped": 0,
            }
            if args[-1] is True
            else {"passed": True}
        ),
    )
    directory = tmp_path / "experiment"
    result = benchmark.prepare(directory, case, contexts, root if separate_source_root else None)
    assert result["trials"] == 9
    manifest = benchmark.verify_freeze(directory)
    assert manifest["source_root"] == str(root)
    assert [item["condition"] for item in manifest["trials"].values()] == [
        "e0",
        "e1",
        "e2",
        "e1",
        "e2",
        "e0",
        "e2",
        "e0",
        "e1",
    ]
    for item in manifest["trials"].values():
        trial = directory / item["path"]
        assert (trial / "sources/policy.md").read_bytes() == resource.read_bytes()
        assert (trial / "src/jev_context/context.py").read_bytes() == (
            source / "context.py"
        ).read_bytes()
        assert (trial / ".git").is_dir()
        expected = (
            b""
            if item["condition"] == "e0"
            else (contexts / f"{item['condition']}.json").read_bytes()
        )
        assert (trial / "context.md").read_bytes() == expected
    (directory / "inputs/e1.json").write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Frozen context"):
        benchmark.verify_freeze(directory)


def test_stdout_does_not_guess_thread_from_other_events():
    result = benchmark.observed_stdout(
        "\n".join(
            [
                '{"type":"thread.started","thread_id":"one"}',
                '{"type":"thread.started","thread_id":"two"}',
                '{"type":"turn.completed","usage":{"input_tokens":100}}',
            ]
        )
    )
    assert result["thread_id"] is None
    assert result["completed_turns"] == 1
