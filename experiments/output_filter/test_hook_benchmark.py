import json
import re

from scripts.hook_benchmark import (
    TASKS,
    hook_metrics,
    hooks_config,
    incident_log,
    prompt,
    session_metrics,
    suite_tests,
)


def test_incident_log_is_deterministic_and_large_when_grepped():
    log = incident_log()
    assert log == incident_log()
    errors = [line for line in log.splitlines() if " ERROR " in line]
    assert len("\n".join(errors).encode()) > 24000  # above the deployed hook threshold
    assert sum("role=required_evidence" in line for line in log.splitlines()) >= 10
    assert sum("packet missing required evidence" in line for line in errors) >= 5


def test_hooks_config_embeds_condition_and_state(tmp_path):
    trial = tmp_path / "trial"
    config = hooks_config(trial, "filter")
    command = config["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert "--mode filter" in command and str(trial / "h") in command
    assert "--min-bytes 24000 --budget-bytes 8000" in command
    assert config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == command
    assert '"' not in command  # PowerShell rejects a quoted executable followed by arguments


def test_prompt_states_task_and_limits():
    for task in TASKS.values():
        text = prompt(task)
        assert task["statement"] in text and "tests/" in text and "MCP" in text


def test_suite_tests_exclude_script_dependent_files():
    names = {path.name for path in suite_tests()}
    assert "test_hook_benchmark.py" not in names and "test_coding_benchmark.py" not in names
    assert "test_tool_hooks.py" not in names


def test_hook_metrics_count_filters_and_saved_output_reads(tmp_path):
    log = tmp_path / "hook-log.jsonl"
    rows = [
        {"event": "PostToolUse", "action": "pass", "bytes": 10, "command": "rg x"},
        {"event": "PostToolUse", "action": "filter", "bytes": 50000, "replacement_bytes": 8000,
         "command": "pytest -q"},
        {"event": "PostToolUse", "action": "pass", "bytes": 900,
         "command": "Get-Content C:\\b\\hook-state\\outputs\\s\\" + "a" * 32 + ".txt"},
        {"event": "error", "error": "x"},
    ]  # fmt: skip
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    metrics = hook_metrics(log)
    assert metrics == {
        "post_tool_use": 3,
        "large_outputs": 1,
        "large_output_bytes": 50000,
        "replacement_bytes": 8000,
        "saved_output_reads": 1,
        "hook_errors": 1,
    }


def test_session_metrics_without_thread_is_empty(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text('{"type": "turn.started"}\nnot json\n', encoding="utf-8")
    assert session_metrics(events) == {"thread_id": None}


def test_seed_locations_exist_once():
    from scripts.hook_benchmark import ROOT

    for task in TASKS.values():
        source = (ROOT / "src/jev_context" / task["file"]).read_text(encoding="utf-8")
        assert source.count(task["before"]) == 1
        assert not re.search(re.escape(task["after"]), source)
