import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import call, record, sync, work

from jev_context.cli import write_config
from jev_context.common import DomainError
from jev_context.policy import Config
from jev_context.service import Service
from jev_context.storage import FileLock


def test_committed_sync_items_survive_killed_process(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.md").write_text("first", encoding="utf-8")
    (root / "b.md").write_text("second", encoding="utf-8")
    config_path = write_config(tmp_path / "config.toml", root, tmp_path / "data", ["*.md"])
    script = """
import sys,time
from jev_context.service import Service
from jev_context.policy import Config
s=Service(Config.load(sys.argv[1]))
original=s.sources.prepare
def prepare(item):
 if item['relative_path']=='b.md':
  print('first_committed',flush=True)
  time.sleep(30)
 return original(item)
s.sources.prepare=prepare
s.call('source_sync',dict(request_id='first',mutation_id='recover',items=[dict(kind='file',relative_path='a.md'),dict(kind='file',relative_path='b.md')]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with ThreadPoolExecutor(1) as pool:
            assert (
                pool.submit(process.stdout.readline).result(timeout=10).strip()
                == b"first_committed"
            )
        process.kill()
        process.communicate(timeout=5)
        s = Service(Config.load(config_path))
        try:
            first_id = s.db.execute("SELECT id FROM source_revisions").fetchone()[0]
            result = call(
                s,
                "source_sync",
                mutation_id="recover",
                items=[
                    dict(kind="file", relative_path="a.md"),
                    dict(kind="file", relative_path="b.md"),
                ],
            )
            assert result["outcome"] == "ok"
            assert result["data"]["items"][0]["revision"] == first_id
            assert s.db.execute("SELECT COUNT(*) FROM source_revisions").fetchone()[0] == 2
        finally:
            s.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_delete_cannot_race_inflight_ingestion(service):
    item = sync(service)
    with FileLock(service.config.db_path.parent / "ingestion.lock"):
        result = call(
            service,
            "data_forget",
            target=dict(kind="source", source_id=item["source_id"]),
            expected_data_revision=service.store.meta()["data_revision"],
        )
        assert result["error"]["code"] == "busy"
    assert service.sources.get(item["source_id"])


def test_unknown_schema_is_not_reset(service):
    service.db.execute("UPDATE project_meta SET schema_version=99")
    with pytest.raises(DomainError, match="Unsupported"):
        Service(service.config)
    assert service.db.execute("SELECT schema_version FROM project_meta").fetchone()[0] == 99


def test_failed_event_rolls_back_everything(service):
    wid = work(service)
    before = service.store.meta()["data_revision"]
    result = record(
        service,
        wid,
        dict(
            kind="decision_proposed",
            text="bad",
            source_refs=[dict(source_id="source-missing", revision="rev-missing")],
        ),
    )
    assert result["outcome"] == "error"
    assert service.store.body("works", wid)["revision"] == 1
    assert service.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert service.store.meta()["data_revision"] == before
