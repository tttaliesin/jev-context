from pathlib import Path

import pytest

from jev_context.common import uid
from jev_context.policy import Config
from jev_context.service import Service


@pytest.fixture
def service(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    instance = Service(Config(root, tmp_path / "data", uid("project"), ("*.md", "*.py", "*.txt")))
    yield instance
    instance.close()


def call(service, tool, **args):
    args.setdefault("request_id", uid("req"))
    if tool in {"source_sync", "work_record", "data_forget"} or (
        tool == "work_open" and "create" in args
    ):
        args.setdefault("mutation_id", uid("mut"))
    return service.call(tool, args)


def work(service, constraints=None):
    result = call(
        service,
        "work_open",
        create=dict(
            title="설계",
            goal="문서 완성",
            scope=dict(mode="design", constraints=constraints or ["설계만 작성"]),
            origin=dict(quote="설계만 작성해줘"),
        ),
    )
    assert result["outcome"] == "ok", result
    return result["data"]["work_id"]


def sync(service, text="권한은 승인 후 부여", path="rules.md"):
    (service.config.project_root / Path(path)).write_text(text, encoding="utf-8", newline="")
    result = call(service, "source_sync", items=[dict(kind="file", relative_path=path)])
    assert result["outcome"] == "ok", result
    return result["data"]["items"][0]


def record(service, wid, event, revision=None, **kwargs):
    if revision is None:
        revision = service.store.body("works", wid)["revision"]
    return call(
        service, "work_record", work_id=wid, expected_revision=revision, event=event, **kwargs
    )
