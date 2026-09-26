import json
import sqlite3
from dataclasses import replace

import pytest
from conftest import call, record, sync, work

from jev_context.common import DomainError, uid
from jev_context.policy import Config
from jev_context.relocation import relocate_project
from jev_context.service import Service
from jev_context.storage import FileLock

HISTORY_TABLES = (
    "works",
    "events",
    "decisions",
    "issues",
    "evidence",
    "sources",
    "source_revisions",
    "source_refs",
    "chunks",
    "lexical",
    "trigrams",
    "tombstones",
)


def history(db):
    return {
        table: [tuple(r) for r in db.execute(f"SELECT * FROM {table}")] for table in HISTORY_TABLES
    }


@pytest.fixture
def moved(service, tmp_path):
    wid = work(service)
    sync(service)
    record(service, wid, dict(kind="decision_proposed", text="기존 결정", source_refs=[]))
    record(
        service,
        wid,
        dict(
            kind="evidence_reported",
            claim="검사 성공",
            target_revision="old",
            observation=dict(
                command="pytest", exit_code=0, result_excerpt="passed", target_hash="old"
            ),
        ),
    )
    result = call(service, "context_prepare", work_id=wid, query="권한")
    assert result["outcome"] == "ok"
    old_root = service.config.project_root
    new_root = tmp_path / "moved-project"
    old_root.rename(new_root)
    config = replace(service.config, project_root=new_root)
    return service, config, old_root, tmp_path / "before-relocation.sqlite", wid


def test_relocation_preserves_wal_history_and_invalidates_cached_results(moved):
    service, config, old_root, backup_path, wid = moved
    before = history(service.db)
    meta = service.store.meta()
    assert service.db.execute("SELECT count(*) FROM packets").fetchone()[0] > 0
    # Keep the original connection open so backup must include committed WAL contents.
    result = relocate_project(config, expected_old_root=old_root, backup_path=backup_path)
    assert result["policy_revision"] == meta["policy_revision"] + 1
    assert result["data_revision"] == meta["data_revision"] + 1
    assert result["source_set_revision"] == meta["source_set_revision"] + 1
    assert history(service.db) == before
    assert service.db.execute("SELECT count(*) FROM packets").fetchone()[0] == 0
    for row in service.db.execute("SELECT result FROM mutations WHERE result IS NOT NULL"):
        replay = json.loads(row[0])
        assert replay["data"]["redacted"]
        assert replay["warnings"][0]["code"] == "project_relocated"
    with sqlite3.connect(backup_path) as backup:
        assert history(backup) == before
        assert backup.execute("SELECT root FROM project_meta").fetchone()[0] == str(old_root)
        assert backup.execute("SELECT count(*) FROM packets").fetchone()[0] > 0
    assert call(service, "work_open", work_id=wid)["error"]["code"] == "policy_denied"
    restored = Service(config)
    try:
        assert call(restored, "work_open", work_id=wid)["data"]["goal"] == "문서 완성"
        assert call(restored, "context_prepare", work_id=wid, query="권한")["outcome"] == "ok"
    finally:
        restored.close()


@pytest.mark.parametrize(
    "mismatch", ["root", "project", "schema", "allowlist", "exclusions", "limit"]
)
def test_relocation_rejects_identity_or_policy_changes_without_backup(moved, mismatch):
    service, config, old_root, backup_path, _ = moved
    expected_root = old_root
    if mismatch == "root":
        expected_root = old_root.parent / "wrong-project"
    elif mismatch == "project":
        # Keep the same database path while testing its stored identity.
        service.db.execute("UPDATE project_meta SET project_id='project-other'")
    elif mismatch == "schema":
        config = replace(config, contract_version="2.0")
    elif mismatch == "allowlist":
        config = replace(config, allowed_paths=("*",))
    elif mismatch == "exclusions":
        config = replace(config, exclude_paths=("rules.md",))
    else:
        config = replace(config, max_file_bytes=config.max_file_bytes + 1)
    meta = service.store.meta()
    before = history(service.db)
    with pytest.raises(DomainError):
        relocate_project(config, expected_old_root=expected_root, backup_path=backup_path)
    assert service.store.meta() == meta
    assert history(service.db) == before
    assert not backup_path.exists()


def test_relocation_never_overwrites_backup(moved):
    service, config, old_root, backup_path, _ = moved
    backup_path.write_bytes(b"prior recovery copy")
    meta = service.store.meta()
    with pytest.raises(FileExistsError):
        relocate_project(config, expected_old_root=old_root, backup_path=backup_path)
    assert backup_path.read_bytes() == b"prior recovery copy"
    assert service.store.meta() == meta


def test_relocation_rolls_back_after_backup_if_invalidation_fails(moved, monkeypatch):
    service, config, old_root, backup_path, _ = moved
    meta = service.store.meta()
    before = history(service.db)
    packet_count = service.db.execute("SELECT count(*) FROM packets").fetchone()[0]

    def fail(db, contract_version):
        db.execute("DELETE FROM packets")
        raise RuntimeError("injected failure after metadata update")

    monkeypatch.setattr("jev_context.relocation._invalidate_cached_results", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        relocate_project(config, expected_old_root=old_root, backup_path=backup_path)
    assert service.store.meta() == meta
    assert history(service.db) == before
    assert service.db.execute("SELECT count(*) FROM packets").fetchone()[0] == packet_count
    with sqlite3.connect(backup_path) as backup:
        assert history(backup) == before
        assert backup.execute("SELECT root FROM project_meta").fetchone()[0] == str(old_root)


def test_relocation_handles_contract_two_and_existing_policy_revision(tmp_path):
    old_root = tmp_path / "old"
    old_root.mkdir()
    config = Config(old_root, tmp_path / "data", uid("project"), ("*.md",), contract_version="2.0")
    original = Service(config)
    result = original.call(
        "work_open",
        dict(
            contract_version="2.0",
            request_id="before",
            mutation_id="create",
            create=dict(
                title="이동 복구",
                goal="기록 보존",
                scope=dict(mode="design", constraints=[]),
                origin=dict(quote="기존 작업을 이어줘"),
            ),
        ),
    )
    assert result["outcome"] == "ok"
    wid = result["data"]["work_id"]
    original.close()
    new_root = tmp_path / "new"
    old_root.rename(new_root)
    config = replace(config, project_root=new_root, policy_revision=2)
    result = relocate_project(
        config, expected_old_root=old_root, backup_path=tmp_path / "backup.sqlite"
    )
    assert result["policy_revision"] == 2
    restored = Service(config)
    try:
        result = restored.call(
            "work_open", dict(contract_version="2.0", request_id="after", work_id=wid)
        )
        assert result["outcome"] == "ok"
        assert result["data"]["goal"] == "기록 보존"
    finally:
        restored.close()


def test_relocation_invalidates_interrupted_sync(moved):
    service, config, old_root, backup_path, _ = moved
    service.db.execute(
        "INSERT INTO mutations(tool,id,input_hash,state,created_at) "
        "VALUES('source_sync','interrupted','old-hash','pending','2026-09-26')"
    )
    relocate_project(config, expected_old_root=old_root, backup_path=backup_path)
    row = service.db.execute("SELECT state,result FROM mutations WHERE id='interrupted'").fetchone()
    assert row["state"] == "complete"
    assert json.loads(row["result"])["outcome"] == "insufficient"
    assert json.loads(row["result"])["data"] == {"redacted": True}


def test_relocation_does_not_start_during_an_existing_write(moved):
    service, config, old_root, backup_path, _ = moved
    service.db.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            relocate_project(config, expected_old_root=old_root, backup_path=backup_path)
    finally:
        service.db.execute("ROLLBACK")
    assert service.store.meta()["root"] == str(old_root)
    assert not backup_path.exists()


@pytest.mark.parametrize("lock_name", ["ingestion.lock", "schema.lock"])
def test_relocation_rejects_active_ingestion_or_schema_operation(moved, lock_name):
    service, config, old_root, backup_path, _ = moved
    meta = service.store.meta()
    before = history(service.db)
    mutations = [tuple(row) for row in service.db.execute("SELECT * FROM mutations")]
    packets = [tuple(row) for row in service.db.execute("SELECT * FROM packets")]
    # Sync reads files outside SQLite transactions while retaining this process lock.
    with FileLock(config.db_path.parent / lock_name):
        with pytest.raises(DomainError, match="Another process owns this operation"):
            relocate_project(config, expected_old_root=old_root, backup_path=backup_path)
    assert service.store.meta() == meta
    assert history(service.db) == before
    assert [tuple(row) for row in service.db.execute("SELECT * FROM mutations")] == mutations
    assert [tuple(row) for row in service.db.execute("SELECT * FROM packets")] == packets
    assert not backup_path.exists()
