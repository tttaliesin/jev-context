from conftest import call, sync, work

from jev_context.common import DomainError, dumps
from jev_context.service import Service


class Engine:
    state = "shadow"
    fingerprint = "fixture-fingerprint"

    def __init__(self, on_call=None):
        self.on_call = on_call

    def evaluate(self, request):
        if self.on_call:
            self.on_call()
        return dict(
            status="observed",
            evaluation_id=request["evaluation_id"],
            answers=[{"choice": "relevant"}],
        )


def test_judge_off_and_unprepared_profile(service):
    sync(service)
    wid = work(service)
    disabled = call(service, "context_prepare", work_id=wid, query="권한")
    assert disabled["outcome"] == "ok"
    assert disabled["data"]["judgment"] == {"status": "skipped", "reason": "profile_disabled"}
    service.config.engine = {"state": "shadow"}
    unprepared = call(service, "context_prepare", work_id=wid, query="권한")
    assert unprepared["outcome"] == "partial"
    assert unprepared["data"]["judgment"] == {
        "status": "abstained",
        "reason": "runtime_profile_not_prepared",
    }
    assert unprepared["data"]["evidence"]
    off = call(service, "context_prepare", work_id=wid, query="권한", judge_mode="off")
    assert off["outcome"] == "ok"
    assert off["data"]["judgment"] == {"status": "skipped", "reason": "requested_off"}


def test_shadow_judgment_is_observed_only_and_engine_failure_is_partial(service):
    sync(service)
    wid = work(service)
    service.config.engine = {"state": "shadow"}
    service.engine = Engine()
    observed = call(service, "context_prepare", work_id=wid, query="권한")
    judgment = observed["data"]["judgment"]
    assert observed["outcome"] == "ok"
    assert judgment["status"] == "observed"
    assert judgment["reason"] == "shadow_only_no_selection_changes"
    assert judgment["profile_fingerprint"] == "fixture-fingerprint"
    assert len(judgment["input_hash"]) == 64

    def fail():
        raise DomainError("engine_unavailable", "Worker stopped")

    service.engine = Engine(fail)
    failed = call(service, "context_prepare", work_id=wid, query="권한")
    assert failed["outcome"] == "partial"
    assert failed["data"]["judgment"] == {"status": "failed", "reason": "engine_unavailable"}
    assert failed["data"]["evidence"] == observed["data"]["evidence"]


def test_required_content_over_budget_returns_only_metadata(service):
    sync(service)
    wid = work(service)
    result = call(service, "context_prepare", work_id=wid, query="권한", budget_bytes=64)
    data = result["data"]
    minimum = data["budget"]["minimum_required_bytes"]
    assert result["outcome"] == "insufficient"
    assert minimum > 64
    assert (data["scope"], data["protected"], data["evidence"]) == ({}, [], [])
    assert data["budget"]["used_bytes"] == 0
    assert data["budget"]["missing_required"] == [
        {"reason": "budget_exceeded", "required_bytes": minimum}
    ]
    assert "omitted_candidates" not in data["coverage"]


def test_candidates_that_do_not_fit_the_budget_are_counted(service):
    sync(service, "권한 규칙 가: " + "가" * 200, "a.md")
    sync(service, "권한 규칙 나: " + "나" * 200, "b.md")
    wid = work(service)
    full = call(service, "context_prepare", work_id=wid, query="권한")
    evidence = full["data"]["evidence"]
    assert len(evidence) == 2
    assert full["data"]["coverage"]["omitted_candidates"] == 0
    # Room for exactly one candidate: adding the second also needs its bytes and a comma.
    budget = full["data"]["budget"]["minimum_required_bytes"] + max(
        len(dumps(e).encode()) for e in evidence
    )
    trimmed = call(service, "context_prepare", work_id=wid, query="권한", budget_bytes=budget)
    data = trimmed["data"]
    assert trimmed["outcome"] == "ok"
    assert len(data["evidence"]) == 1
    assert data["coverage"]["omitted_candidates"] == 1
    assert data["budget"]["used_bytes"] <= budget


def test_source_set_change_during_preparation_returns_no_old_bytes(service):
    sync(service, "권한: 승인 후 배포")
    wid = work(service)

    def add_source():
        other = Service(service.config)
        try:
            sync(other, "새 규칙", "new.md")
        finally:
            other.close()

    service.config.engine = {"state": "shadow"}
    service.engine = Engine(add_source)
    result = call(service, "context_prepare", work_id=wid, query="권한")
    data = result["data"]
    assert result["outcome"] == "insufficient"
    assert (data["scope"], data["protected"], data["evidence"]) == ({}, [], [])
    assert data["judgment"] == {"status": "abstained", "reason": "source_set_changed"}
    assert data["budget"]["used_bytes"] == 0
    assert data["budget"]["missing_required"] == [{"reason": "source_set_changed"}]
