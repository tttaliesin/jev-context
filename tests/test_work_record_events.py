from conftest import call, record, sync, work


def test_issue_resolution_stops_protection_and_cannot_repeat(service):
    item = sync(service)
    ref = dict(source_id=item["source_id"], revision=item["revision"])
    wid = work(service)
    opened = record(
        service,
        wid,
        dict(kind="issue_opened", issue_kind="conflict", text="배포 조건 충돌", source_refs=[ref]),
    )
    issue_id = opened["data"]["issue_id"]
    packet = call(service, "context_prepare", work_id=wid, query="무관한 질문")
    assert [p["id"] for p in packet["data"]["protected"] if p["role"] == "known_issue"] == [
        issue_id
    ]
    resolve = dict(kind="issue_resolved", issue_id=issue_id, resolution="승인 후 배포로 합의")
    resolved = record(service, wid, {**resolve, "source_refs": []})
    assert resolved["outcome"] == "ok"
    issue = call(service, "work_open", work_id=wid)["data"]["issues"][0]
    assert (issue["status"], issue["resolution"]) == ("resolved_reported", "승인 후 배포로 합의")
    packet = call(service, "context_prepare", work_id=wid, query="무관한 질문")
    assert not [p for p in packet["data"]["protected"] if p["role"] == "known_issue"]
    again = record(service, wid, {**resolve, "source_refs": []})
    assert again["error"]["code"] == "invalid_argument"
    assert service.store.body("works", wid)["revision"] == 3


def test_only_reported_complete_work_can_be_reopened(service):
    wid = work(service)
    reopen = dict(kind="work_reopened", reason="추가 요청", origin=dict(quote="다시 열어줘"))
    active = record(service, wid, reopen)
    assert active["error"]["code"] == "invalid_argument"
    completed = record(
        service,
        wid,
        dict(kind="completion_reported", summary="설계 완료", evidence_ids=[], open_items=[]),
    )
    assert completed["outcome"] == "ok"
    assert service.store.body("works", wid)["status"] == "completion_reported"
    reopened = record(service, wid, reopen)
    assert reopened["outcome"] == "ok"
    body = service.store.body("works", wid)
    assert (body["status"], body["completion_coverage"]) == ("active", "incomplete")
    events = call(service, "work_inspect", work_id=wid, view="events")["data"]["items"]
    assert [e["kind"] for e in events][-1] == "work_reopened"
