from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import call, record, work

from jev_context.common import dumps
from jev_context.service import Service


def test_restart_and_provenance(service):
    wid = work(service)
    other = Service(service.config)
    try:
        result = call(other, "work_open", work_id=wid)["data"]
        assert result["scope"]["mode"] == "design"
        assert result["origin"]["quote"] == "설계만 작성해줘"
        assert result["provenance"] == "agent_reported"
        assert result["scope"]["allowed_actions"] == ["read", "write_design"]
    finally:
        other.close()


def test_mutation_retry_mismatch_and_inspect(service):
    wid = work(service)
    event = dict(kind="progress_reported", summary="작업 중", next_actions=[], source_refs=[])
    a = record(service, wid, event, revision=1, mutation_id="stable")
    b = record(service, wid, event, revision=1, mutation_id="stable")
    assert a["data"] == b["data"]
    assert a["request_id"] != b["request_id"]
    c = record(service, wid, {**event, "summary": "다른 작업"}, revision=1, mutation_id="stable")
    assert c["error"]["code"] == "idempotency_mismatch"
    inspected = call(service, "work_inspect", work_id=wid, view="mutation", mutation_id="stable")
    assert inspected["data"]["mutations"][0]["result"]["data"] == a["data"]
    assert len(call(service, "work_inspect", work_id=wid, view="events")["data"]["items"]) == 2


def test_concurrent_revision_only_one_writer(service):
    wid = work(service)

    def worker(index):
        s = Service(service.config)
        try:
            return record(
                s,
                wid,
                dict(kind="progress_reported", summary=str(index), next_actions=[], source_refs=[]),
                revision=1,
            )
        finally:
            s.close()

    # Initialize workers sequentially; schema-lock contention is separately tested.
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(worker, range(2)))
    assert sorted(r["outcome"] for r in results) == ["conflict", "ok"]
    assert service.store.body("works", wid)["revision"] == 2


def test_decision_lifecycle_cross_work_refs_and_completion(service):
    wid = work(service)
    a = record(service, wid, dict(kind="decision_proposed", text="A", source_refs=[]))["data"][
        "decision_id"
    ]
    b = record(service, wid, dict(kind="decision_proposed", text="B", source_refs=[]))["data"][
        "decision_id"
    ]
    for did in (a, b):
        assert (
            record(
                service,
                wid,
                dict(kind="decision_adopted", decision_id=did, origin=dict(quote="채택")),
            )["outcome"]
            == "ok"
        )
    assert (
        record(
            service,
            wid,
            dict(
                kind="decision_superseded",
                decision_id=a,
                replacement_id=b,
                origin=dict(quote="B만 유지"),
            ),
        )["outcome"]
        == "ok"
    )
    states = call(service, "work_inspect", work_id=wid, view="decisions")["data"]["items"]
    assert [d["status"] for d in states] == ["superseded", "adopted_reported"]
    other = work(service)
    assert (
        record(
            service, other, dict(kind="decision_adopted", decision_id=b, origin=dict(quote="채택"))
        )["error"]["code"]
        == "not_found"
    )
    evidence = record(
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
    )["data"]["evidence_id"]
    assert (
        record(
            service,
            wid,
            dict(
                kind="completion_reported",
                summary="완료 보고",
                evidence_ids=[evidence],
                open_items=[],
            ),
        )["outcome"]
        == "ok"
    )
    result = call(service, "work_open", work_id=wid)["data"]
    assert result["status"] == "completion_reported"
    assert result["completion_coverage"] == "incomplete"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {"request_id": "x", "unexpected": 1},
        {"request_id": "x", "limit": True},
        {"request_id": "x", "limit": 0},
    ],
)
def test_strict_validation(service, bad):
    assert service.call("workspace_status", bad)["error"]["code"] == "invalid_argument"


def test_scope_is_explicit_and_korean_not_inferred(service):
    phrases = [
        "설계만",
        "하면 안 돼",
        "안 해도 돼",
        "그건 빼고",
        "이 PR 보안만 리뷰해줘",
        "실패하지 않았다는 증거는 아직 없어",
    ]
    wid = work(service, phrases)
    packet = call(service, "context_prepare", work_id=wid, query="이어서 해줘")
    assert packet["data"]["scope"]["constraints"] == phrases
    assert packet["data"]["scope"]["mode"] == "design"
    assert packet["data"]["judgment"]["status"] == "skipped"
    assert all(p in dumps(packet) for p in phrases)


def test_cursor_detects_changes(service):
    work(service)
    work(service)
    page = call(service, "workspace_status", limit=1)["data"]
    work(service)
    assert (
        call(service, "workspace_status", cursor=page["next_cursor"])["error"]["code"]
        == "cursor_stale"
    )


def test_read_only_denies_mutations(service):
    wid = work(service)
    service.config.read_only = True
    assert (
        record(
            service,
            wid,
            dict(kind="progress_reported", summary="x", next_actions=[], source_refs=[]),
        )["error"]["code"]
        == "policy_denied"
    )
    assert call(service, "context_prepare", work_id=wid, query="설계")["outcome"] == "ok"
    assert service.db.execute("SELECT COUNT(*) FROM packets").fetchone()[0] == 0


def test_storage_budget_does_not_report_success(service):
    service.config.storage_limit_bytes = 1
    result = call(
        service,
        "work_open",
        create=dict(
            title="x", goal="x", scope=dict(mode="design", constraints=[]), origin=dict(quote="x")
        ),
    )
    assert result["error"]["code"] == "storage_limit"
    assert service.db.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 0


def test_unobserved_command_cannot_become_verified_completion(service):
    wid = work(service)
    result = record(
        service,
        wid,
        dict(
            kind="evidence_reported",
            claim="실행 여부 미확인",
            target_revision="current",
            observation=dict(status="outcome_unknown", details="검사 명령은 제안됐지만 결과 없음"),
        ),
    )
    evidence_id = result["data"]["evidence_id"]
    record(
        service,
        wid,
        dict(
            kind="completion_reported",
            summary="완료라고 보고",
            evidence_ids=[evidence_id],
            open_items=[],
        ),
    )
    assert call(service, "work_open", work_id=wid)["data"]["completion_coverage"] == "incomplete"
    evidence = call(service, "work_inspect", work_id=wid, view="evidence")["data"]["items"][0]
    assert evidence["observation"]["status"] == "outcome_unknown"
    assert "exit_code" not in evidence["observation"]
