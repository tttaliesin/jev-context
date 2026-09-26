import json
import subprocess
import sys
from dataclasses import replace

from conftest import sync, work

from jev_context.session_hooks import handle


def start(source="startup"):
    return {"hook_event_name": "SessionStart", "session_id": "s", "source": source}


def prompt(turn="t1"):
    return {"hook_event_name": "UserPromptSubmit", "session_id": "s", "turn_id": turn,
            "prompt": "설계 이어서"}  # fmt: skip


def context(result):
    return result["hookSpecificOutput"]["additionalContext"]


def test_session_start_lists_verified_ids_without_stored_text(service):
    first, second = work(service), work(service)
    result, record = handle(start(), service.config)
    text = context(result)
    assert second in text and first in text and text.index(second) < text.index(first)
    # Titles, goals, constraints and quotes are user text; they must stay out of developer context.
    for stored in ("설계", "문서 완성", "설계만 작성"):
        assert stored not in text
    assert record["action"] == "restore_hint" and record["works"] == 2


def test_compacted_session_is_told_to_restore_decisions(service):
    work(service)
    assert context(handle(start("compact"), service.config)[0]).startswith(
        "[jev-context] Context was just compacted."
    )


def test_no_store_or_no_work_prints_nothing(service, tmp_path):
    assert handle(start(), service.config)[1]["action"] == "no_work"
    empty = replace(service.config, data_root=tmp_path / "never-created")
    assert handle(start(), empty) == (
        None,
        {"event": "SessionStart", "turn_id": None, "action": "no_store"},
    )


def test_prompt_signals_changed_sources_once_per_turn_without_paths(service):
    sync(service, "원문", "rules.md")
    assert handle(prompt("t1"), service.config)[1]["action"] == "current"
    (service.config.project_root / "rules.md").write_text("바뀐 원문", encoding="utf-8")
    result, record = handle(prompt("t2"), service.config)
    assert record["changed_sources"] == 1 and "1 registered source file(s)" in context(result)
    assert "stale_sources" in context(result) and "rules.md" not in context(result)
    assert handle(prompt("t2"), service.config) == (
        None,
        {"event": "UserPromptSubmit", "turn_id": "t2", "action": "duplicate"},
    )


def test_workspace_status_lists_the_sources_the_hook_counts(service):
    sync(service, "원문", "rules.md")
    sync(service, "그대로", "keep.md")
    (service.config.project_root / "rules.md").write_text("바뀐 원문", encoding="utf-8")
    hinted = handle(prompt(), service.config)[1]["changed_sources"]
    status = service.call("workspace_status", {"request_id": "r"})["data"]["stale_sources"]
    assert status["count"] == hinted == 1 and status["checked"] == 2
    assert [(i["relative_path"], i["reason"]) for i in status["items"]] == [("rules.md", "changed")]
    assert status["truncated"] is False
    (service.config.project_root / "keep.md").unlink()
    reasons = {
        i["relative_path"]: i["reason"]
        for i in service.call("workspace_status", {"request_id": "r2"})["data"]["stale_sources"][
            "items"
        ]
    }
    assert reasons["rules.md"] == "changed" and reasons["keep.md"] != "changed"


def test_deleted_source_file_counts_as_changed(service):
    sync(service, "원문", "rules.md")
    (service.config.project_root / "rules.md").unlink()
    assert handle(prompt(), service.config)[1]["changed_sources"] == 1


def test_other_events_are_ignored(service):
    work(service)
    assert handle({"hook_event_name": "PostToolUse"}, service.config)[0] is None


def test_entry_point_prints_nothing_on_bad_input_and_logs(service, tmp_path):
    work(service)
    config = service.config
    path = tmp_path / "project.toml"
    path.write_text(
        "\n".join(
            [
                f"project_root = {json.dumps(str(config.project_root))}",
                f"data_root = {json.dumps(str(config.data_root))}",
                f"project_id = {json.dumps(config.project_id)}",
                f"allowed_paths = {json.dumps(list(config.allowed_paths))}",
            ]
        ),
        encoding="utf-8",
    )
    command = [sys.executable, "-m", "jev_context.session_hooks", "--config", str(path)]
    bad = subprocess.run(command, input=b"not json", capture_output=True)
    assert bad.returncode == 0 and bad.stdout == b""
    good = subprocess.run(command, input=json.dumps(start()).encode(), capture_output=True)
    assert json.loads(good.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    events = (config.db_path.parent / "hooks" / "events.jsonl").read_text(encoding="utf-8")
    assert '"event": "error"' in events and '"action": "restore_hint"' in events
