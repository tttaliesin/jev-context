"""A hook must not expose another project root's work through a reused project ID."""

from dataclasses import replace

import pytest

from jev_context.common import uid
from jev_context.policy import Config
from jev_context.service import Service
from jev_context.session_hooks import handle, open_store


@pytest.fixture
def bound_store(tmp_path):
    """Use real public calls without depending on the repository's conftest fixtures."""
    root = tmp_path / "project"
    root.mkdir()
    service = Service(Config(root, tmp_path / "data", uid("project"), ("*.md",)))
    try:
        opened = service.call(
            "work_open",
            {
                "request_id": uid("req"),
                "mutation_id": uid("mut"),
                "create": {
                    "title": "보존할 기존 작업",
                    "goal": "프로젝트 연결 확인",
                    "scope": {"mode": "design", "constraints": ["원문 보존"]},
                    "origin": {"quote": "프로젝트 연결 확인"},
                },
            },
        )
        assert opened["outcome"] == "ok", opened
        (root / "rules.md").write_text("원래 프로젝트의 보존할 근거", encoding="utf-8")
        synced = service.call(
            "source_sync",
            {
                "request_id": uid("req"),
                "mutation_id": uid("mut"),
                "items": [{"kind": "file", "relative_path": "rules.md"}],
            },
        )
        assert synced["outcome"] == "ok", synced
        other_root = tmp_path / "different-project"
        other_root.mkdir()
        other_config = replace(service.config, project_root=other_root)
        assert other_config.project_id == service.config.project_id
        assert other_config.db_path == service.config.db_path
        yield service, other_config, opened["data"]["work_id"]
    finally:
        service.close()


def contents(service):
    """Include metadata, work bodies, revisions and evidence in the preservation check."""
    return tuple(service.store.db.iterdump())


def test_open_store_rejects_same_project_id_bound_to_another_root(bound_store):
    service, other_config, _ = bound_store
    before = contents(service)
    db = open_store(other_config)
    try:
        assert db is None
    finally:
        if db is not None:
            db.close()
        assert contents(service) == before


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit"])
def test_hook_emits_no_hint_for_a_different_project_root(bound_store, event):
    service, other_config, _ = bound_store
    before = contents(service)
    result, record = handle({"hook_event_name": event}, other_config)
    assert contents(service) == before
    assert result is None
    assert record == {"event": event, "turn_id": None, "action": "no_store"}


def test_open_store_accepts_matching_root_without_changing_existing_data(bound_store):
    service, _, work_id = bound_store
    before = contents(service)
    db = open_store(service.config)
    assert db is not None
    try:
        meta = db.execute("SELECT project_id, root FROM project_meta").fetchone()
        assert meta["project_id"] == service.config.project_id
        assert meta["root"] == str(service.config.project_root)
        assert db.execute("SELECT id FROM works WHERE id=?", (work_id,)).fetchone() is not None
    finally:
        db.close()
    assert contents(service) == before


def test_hook_preserves_matching_project_restore_and_sync_flow(bound_store):
    service, _, work_id = bound_store
    before = contents(service)
    restored, restore_record = handle({"hook_event_name": "SessionStart"}, service.config)
    assert restore_record["action"] == "restore_hint"
    assert work_id in restored["hookSpecificOutput"]["additionalContext"]

    (service.config.project_root / "rules.md").write_text("바뀐 근거", encoding="utf-8")
    updated, sync_record = handle({"hook_event_name": "UserPromptSubmit"}, service.config)
    assert sync_record["action"] == "sync_hint"
    assert sync_record["changed_sources"] == 1
    assert "1 registered source file(s)" in updated["hookSpecificOutput"]["additionalContext"]
    assert contents(service) == before
