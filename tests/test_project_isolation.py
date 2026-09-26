import os
import subprocess

import pytest
from conftest import call, sync, work

from jev_context.common import uid
from jev_context.policy import Config
from jev_context.service import Service


def test_same_filename_in_other_project_is_never_returned(service, tmp_path):
    first = sync(service, "권한: 현재 프로젝트")
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = Service(Config(other_root, tmp_path / "other-data", uid("project"), ("*.md",)))
    try:
        second = sync(other, "권한: 다른 프로젝트의 비공개 정보")
        assert (
            call(
                service, "source_read", source_id=second["source_id"], revision=second["revision"]
            )["error"]["code"]
            == "not_found"
        )
        packet = call(service, "context_prepare", work_id=work(service), query="권한")
        assert [e["source_id"] for e in packet["data"]["evidence"]] == [first["source_id"]]
    finally:
        other.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_windows_junction_escape_without_symlink_privilege(service, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.md").write_text("private", encoding="utf-8")
    link = service.config.project_root / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
    )
    assert result.returncode == 0, result.stderr
    try:
        answer = call(
            service, "source_sync", items=[dict(kind="file", relative_path="junction/data.md")]
        )
        assert answer["data"]["items"][0]["error"]["code"] == "policy_denied"
    finally:
        # Remove only the directory junction, never recurse into its target.
        link.rmdir()
    assert (outside / "data.md").exists()
