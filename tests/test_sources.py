import unicodedata

import pytest
from conftest import call, sync, work


@pytest.mark.parametrize(
    "query",
    ["권한", "승인", "refreshToken", "권한 refreshToken()", unicodedata.normalize("NFD", "권한")],
)
def test_korean_short_mixed_and_nfc_search(service, query):
    item = sync(service, "# 권한\nrefreshToken()은 승인 후 변경\n")
    packet = call(service, "context_prepare", work_id=work(service), query=query)
    assert packet["outcome"] == "ok"
    assert packet["data"]["evidence"][0]["source_id"] == item["source_id"]
    assert packet["data"]["coverage"]["scope"] == "registered_sources"


def test_changed_missing_and_reverted_sources(service):
    first = sync(service, "권한: 배포 가능")
    wid = work(service)
    path = service.config.project_root / "rules.md"
    path.write_text("권한: 승인 후 배포", encoding="utf-8")
    packet = call(service, "context_prepare", work_id=wid, query="권한")
    assert "승인 후" in packet["data"]["evidence"][0]["text"]
    assert packet["data"]["evidence"][0]["revision"] != first["revision"]
    old = call(service, "source_read", source_id=first["source_id"], revision=first["revision"])
    assert old["data"]["freshness"] == "historical"
    reverted = sync(service, "권한: 배포 가능")
    assert reverted["revision"] == first["revision"]
    path.unlink()
    packet = call(service, "context_prepare", work_id=wid, query="권한")
    assert not packet["data"]["evidence"]
    historical = call(
        service, "source_read", source_id=first["source_id"], revision=first["revision"]
    )
    assert historical["data"]["source_status"] == "missing"


def test_line_paging_and_long_line(service):
    item = sync(service, "가\n나\n다\n")
    page = call(
        service, "source_read", source_id=item["source_id"], revision=item["revision"], max_bytes=4
    )["data"]
    assert page["text"] == "가\n"
    assert page["next_start_line"] == 2 and not page["eof"]
    item = sync(service, "가" * 12000)
    result = call(
        service,
        "source_read",
        source_id=item["source_id"],
        revision=item["revision"],
        max_bytes=32768,
    )
    assert result["error"]["code"] == "line_too_large"
    rows = service.db.execute(
        "SELECT text,byte_start,byte_end FROM chunks WHERE revision=? ORDER BY byte_start",
        (item["revision"],),
    ).fetchall()
    assert all(len(r["text"].encode()) <= 4096 for r in rows)
    assert "".join(r["text"] for r in rows) == "가" * 12000


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "C:/outside.md",
        ".env.txt",
        ".git/config.txt",
        "secrets.md",
        "weights.bin",
        "rules.md:stream",
    ],
)
def test_path_policy(service, path):
    result = call(service, "source_sync", items=[dict(kind="file", relative_path=path)])
    assert result["outcome"] == "partial"
    assert result["data"]["items"][0]["error"]["code"] == "policy_denied"


def test_symlink_escape(service, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    link = service.config.project_root / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("OS does not permit creating symlinks in this test session")
    result = call(service, "source_sync", items=[dict(kind="file", relative_path="link.md")])
    assert result["data"]["items"][0]["error"]["code"] == "policy_denied"


def test_excerpt_origin_is_reported_and_key_cannot_change_origin(service):
    item = dict(
        kind="excerpt",
        text="PDF 발췌",
        origin_label="PDF A",
        external_key="pdf-a",
        origin_url="https://example.invalid/a.pdf",
    )
    first = call(service, "source_sync", items=[item])["data"]["items"][0]
    assert first["provenance"] == "agent_reported"
    second = call(service, "source_sync", items=[{**item, "origin_label": "PDF B"}])
    assert second["data"]["items"][0]["error"]["code"] == "invalid_argument"
    text = call(service, "source_read", source_id=first["source_id"], revision=first["revision"])[
        "data"
    ]
    assert text["origin_kind"] == "supplied_excerpt"


def test_sync_partial_replay_and_unprocessed(service):
    service.config.timeout_seconds = 0
    args = dict(
        items=[dict(kind="excerpt", text="a", origin_label="a", external_key="a")],
        mutation_id="partial",
    )
    result = call(service, "source_sync", **args)
    assert result["data"]["unprocessed"] == [0]
    service.config.timeout_seconds = 5
    replay = call(service, "source_sync", **args)
    assert replay["data"] == result["data"]
    assert service.db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


def test_last_exception_in_long_document_is_retrieved(service):
    text = ("일반 설명과 배경\n" * 2000) + "끝부분 예외: 권한 승인 전 로컬 검사는 가능\n"
    item = sync(service, text)
    packet = call(service, "context_prepare", work_id=work(service), query="끝부분 예외 권한")
    match = next(e for e in packet["data"]["evidence"] if "끝부분 예외" in e["text"])
    assert match["end_line"] == 2001
    assert match["revision"] == item["revision"]


def test_unregistered_files_are_not_collected_by_search(service):
    (service.config.project_root / "unregistered.md").write_text("권한", encoding="utf-8")
    packet = call(service, "context_prepare", work_id=work(service), query="권한")
    assert not packet["data"]["evidence"]
    assert packet["data"]["coverage"]["scope"] == "registered_sources"
    assert packet["data"]["coverage"]["claim"]


def test_opposite_documents_are_returned_without_choosing_winner(service):
    first = sync(service, "권한: 배포 가능", "allow.md")
    second = sync(service, "권한: 배포 금지", "deny.md")
    packet = call(service, "context_prepare", work_id=work(service), query="권한 배포")
    assert {e["source_id"] for e in packet["data"]["evidence"]} == {
        first["source_id"],
        second["source_id"],
    }
    assert packet["data"]["judgment"]["status"] == "skipped"
