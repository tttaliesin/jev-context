import shutil
import sqlite3
from dataclasses import replace

import pytest

from jev_context.budget import measure
from jev_context.common import DomainError, digest, dumps, now, uid
from jev_context.coordination import inventory_hash
from jev_context.policy import Config
from jev_context.service import CONTRACT_V2, Service
from jev_context.storage import Store


@pytest.fixture
def v2(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    service = Service(
        Config(root, tmp_path / "data", uid("project"), ("*.md",), contract_version="2.0")
    )
    yield service
    service.close()


def call(service, tool, **args):
    args.setdefault("request_id", uid("req"))
    args.setdefault("contract_version", "2.0")
    if tool in {"source_sync", "work_record", "data_forget"} or "create" in args:
        args.setdefault("mutation_id", uid("mut"))
    result = service.call(tool, args)
    from jsonschema import validate

    validate(result, CONTRACT_V2["output"])
    return result


def work(service, constraints=None):
    return call(
        service,
        "work_open",
        create={
            "title": "로그인 수정",
            "goal": "로그인 오류 해결",
            "scope": {"mode": "implement", "constraints": constraints or ["원문과 반대 근거 보존"]},
            "origin": {"quote": "구현해줘"},
        },
    )["data"]["work_id"]


def record(service, wid, event, **args):
    return call(
        service,
        "work_record",
        work_id=wid,
        expected_revision=service.work(wid)["revision"],
        event=event,
        **args,
    )


def sync(service, text, path="rules.md"):
    (service.config.project_root / path).write_text(text, encoding="utf-8")
    return call(service, "source_sync", items=[{"kind": "file", "relative_path": path}])["data"][
        "items"
    ][0]


class Judge:
    fingerprint = "test-profile"
    state = "shadow"

    def __init__(self, on_call=None, choice="irrelevant"):
        self.on_call, self.choice, self.calls = on_call, choice, 0

    def evaluate(self, request):
        self.calls += 1
        if self.on_call:
            self.on_call()
        answers = []
        for q in request["questions"]:
            choice = (
                self.choice
                if q["purpose"] == "relevance"
                else "fit"
                if q["purpose"] == "capability_fit"
                else "unrelated"
                if q["purpose"] == "evidence_relation"
                else "hide"
            )
            answers.append({"question_id": q["id"], "choice": choice, "raw_confidence": 0.99})
        return {"status": "observed", "answers": answers, "latency_ms": 1}


def activate(service, tmp_path):
    report = {
        "profile_fingerprint": "test-profile",
        "dataset_provenance": "human_reviewed",
        "split": "heldout",
        "gates": {
            f"{p}:ko": {
                "count": 30,
                "critical_regressions": 0,
                "quality_pass": True,
                "efficiency_pass": True,
                "non_abstained": 30,
                "threshold": 0.9,
            }
            for p in ("relevance", "evidence_relation", "presentation", "capability_fit")
        },
    }
    # A fixture for testing gates, never a product quality evaluation.
    path = tmp_path / "fixture-report.json"
    path.write_text(dumps(report), encoding="utf-8")
    service.config.engine = {
        "state": "active",
        "evaluation_file": str(path),
        "evaluation_sha256": digest(path.read_bytes()),
    }
    return path


def test_contract_requires_explicit_version_and_v1_is_unchanged(v2):
    assert v2.call("workspace_status", {"request_id": "old"})["outcome"] == "error"
    assert call(v2, "workspace_status")["contract_version"] == "2.0"
    assert len(CONTRACT_V2["tools"]) == 10


def test_migration_backup_restore_and_old_version_refusal(service):
    from conftest import work as old_work

    wid = old_work(service)
    oldconfig = service.config
    config = replace(oldconfig, contract_version="2.0")
    with pytest.raises(DomainError):
        Store(config)
    service.close()
    migrated = Store(config, migrate=True)
    assert migrated.meta()["schema_version"] == 2
    backup = migrated.path.with_suffix(".v1-backup.sqlite")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT schema_version FROM project_meta").fetchone()[0] == 1
        assert connection.execute("SELECT id FROM works").fetchone()[0] == wid
    migrated.close()
    with pytest.raises(DomainError):
        Store(oldconfig)
    restore_config = replace(oldconfig, data_root=oldconfig.data_root / "restored")
    restore_config.db_path.parent.mkdir(parents=True)
    shutil.copy2(backup, restore_config.db_path)
    restored = Store(restore_config)
    assert restored.body("works", wid)["goal"]
    restored.close()


def test_model_selection_protects_required_and_shadow_preserves(v2, tmp_path):
    wid = work(v2)
    source = sync(v2, "로그인 토큰은 만료되지 않았다. 반대 근거.")
    v2.engine = Judge()
    v2.config.engine = {"state": "shadow"}
    args = {"work_id": wid, "query": "로그인", "budget_bytes": 16384}
    shadow = call(v2, "context_prepare", **args)
    assert shadow["data"]["evidence"]
    activate(v2, tmp_path)
    active = call(v2, "context_prepare", **args)
    assert not active["data"]["evidence"]
    assert active["data"]["judgment"]["status"] == "applied"
    # Even an omitted source remains linked to the stored request for deletion policy.
    pid = active["data"]["packet_id"]
    stored = v2.store.body("packets", pid)
    evaluation = stored["judgment"]["evaluations"][0]
    assert evaluation["request"]["state"]["query"] == "로그인"
    assert (
        evaluation["request"]["state"]["candidate"] == "로그인 토큰은 만료되지 않았다. 반대 근거."
    )
    assert evaluation["input_hash"] == digest(dumps(evaluation["request"]["state"]).encode())
    assert evaluation["request_dispatched"]
    assert source["source_id"] in {r["source_id"] for r in v2.store.inherited_refs("packets", pid)}
    assert "evaluations" not in active["data"]["judgment"]
    required = call(
        v2,
        "context_prepare",
        required_refs=[{k: source[k] for k in ("source_id", "revision")}],
        **args,
    )
    assert required["data"]["evidence"][0]["role"] == "required_evidence"


def test_observed_shadow_judgment_keeps_ok_outcome(v2):
    wid = work(v2)
    sync(v2, "로그인 실패의 원인은 데이터베이스 연결 오류다.")
    v2.config.engine = {"state": "shadow"}
    args = {"work_id": wid, "query": "로그인", "judge_mode": "required"}
    unprepared = call(v2, "context_prepare", **args)
    assert unprepared["data"]["judgment"]["status"] == "abstained"
    assert unprepared["outcome"] == "insufficient"
    v2.engine = Judge(choice="relevant")
    observed = call(v2, "context_prepare", **args)
    assert observed["data"]["judgment"]["status"] == "observed"
    assert observed["outcome"] == "ok"


def test_profile_report_change_revokes_selection(v2, tmp_path):
    wid = work(v2)
    sync(v2, "로그인 관련 자료")
    v2.engine = Judge()
    report = activate(v2, tmp_path)
    report.write_text("{}")
    result = call(v2, "context_prepare", work_id=wid, query="로그인")
    assert result["data"]["evidence"]
    assert result["data"]["judgment"]["mode"] == "shadow"


def test_change_during_model_discards_judgment(v2, tmp_path):
    wid = work(v2)
    sync(v2, "로그인 이전 파일")
    v2.engine = Judge(
        lambda: (v2.config.project_root / "rules.md").write_text("로그인 변경", encoding="utf-8")
    )
    activate(v2, tmp_path)
    result = call(v2, "context_prepare", work_id=wid, query="로그인")
    assert result["data"]["judgment"]["reason"] == "source_changed"
    assert result["data"]["evidence"]


def test_unapplied_shadow_purposes_can_be_omitted_until_promoted(v2, tmp_path):
    wid = work(v2)
    sync(v2, "로그인 관련 자료")
    asked = []

    class Recording(Judge):
        profile = {"omit_shadow_purposes": ["presentation"]}

        def evaluate(self, request):
            asked.append(sorted(q["purpose"] for q in request["questions"]))
            return super().evaluate(request)

    v2.engine = Recording(choice="relevant")
    v2.config.engine = {"state": "shadow"}
    call(v2, "context_prepare", work_id=wid, query="로그인")
    assert asked == [["relevance"]]
    activate(v2, tmp_path)  # a promoted presentation purpose is applied, so it is asked again
    call(v2, "context_prepare", work_id=wid, query="로그인")
    assert asked[-1] == ["presentation", "relevance"]


def test_historical_required_evidence_does_not_discard_judgment(v2):
    # V1 Desktop demo: a work's required refs pointed at older revisions of files edited
    # since, and every judgment was discarded as "source_changed".
    wid = work(v2)
    old = sync(v2, "로그인 이전 결정: v3 미반영", "decision.md")
    sync(v2, "로그인 관련 현재 자료", "rules.md")
    sync(v2, "로그인 이후 결정: v3 미반영 유지, 문서 갱신", "decision.md")
    v2.engine = Judge(choice="relevant")
    v2.config.engine = {"state": "shadow"}
    result = call(
        v2,
        "context_prepare",
        work_id=wid,
        query="로그인",
        required_refs=[{k: old[k] for k in ("source_id", "revision")}],
    )
    data = result["data"]
    assert data["judgment"]["status"] == "observed"
    pinned = [e for e in data["evidence"] if e["revision"] == old["revision"]]
    assert pinned and pinned[0]["freshness"] == "historical"


@pytest.mark.parametrize("budget", [1024, 2048, 4096, 16384])
def test_whole_envelope_budget_and_no_silent_mandatory_loss(v2, budget):
    wid = work(v2, ["반드시 보존 " * 80])
    sync(v2, "로그인 " * 400)
    result = call(v2, "context_prepare", work_id=wid, query="로그인", budget_bytes=budget)
    assert measure(result) <= budget
    assert result["data"]["wire_budget"]["used_bytes"] == measure(result)
    if result["outcome"] != "insufficient":
        assert "반드시 보존" in dumps(result["data"]["scope"])


def inventory():
    return {
        "revision": "inv-1",
        "observed_at": now(),
        "complete": True,
        "items": [
            {
                "id": "read",
                "kind": "tool",
                "description": "파일 읽기",
                "version": "1",
                "available": True,
                "mandatory": False,
            },
            {
                "id": "rules",
                "kind": "skill",
                "description": "필수 개발 지침",
                "version": "1",
                "available": True,
                "mandatory": True,
            },
        ],
    }


def test_capability_freshness_version_mandatory_and_no_execution(v2, tmp_path):
    wid = work(v2)
    v2.engine = Judge()
    activate(v2, tmp_path)
    inv = inventory()
    result = call(v2, "capability_recommend", work_id=wid, query="코드 조사", inventory=inv)
    assert {i["id"] for i in result["data"]["selected"]} == {"read", "rules"}
    assert result["data"]["execution_authorized"] is False
    assert "request" not in result["data"]["evaluations"][0]
    pid = result["data"]["inspection"]["packet_id"]
    stored = v2.store.body("packets", pid)
    requests = [r["request"] for r in stored["judgment"]["evaluations"]]
    assert requests[0]["state"]["candidate"] == inv["items"][0]
    assert requests[0]["questions"][0]["purpose"] == "capability_fit"
    old_hash = inventory_hash(inv)
    inv["items"][0]["version"] = "2"
    assert (
        call(
            v2,
            "capability_recommend",
            work_id=wid,
            query="조사",
            inventory=inv,
            expected_inventory_hash=old_hash,
        )["outcome"]
        == "conflict"
    )
    inv["observed_at"] = "2000-01-01T00:00:00Z"
    assert (
        call(v2, "capability_recommend", work_id=wid, query="조사", inventory=inv)["outcome"]
        == "error"
    )


def test_unavailable_required_capability_never_reaches_model(v2, tmp_path):
    wid = work(v2)
    v2.engine = Judge()
    activate(v2, tmp_path)
    inv = inventory()
    inv["items"] = [{**inv["items"][0], "available": False}]
    result = call(
        v2,
        "capability_recommend",
        work_id=wid,
        query="파일 읽기",
        inventory=inv,
        required_ids=["read"],
    )
    assert result["outcome"] == "insufficient"
    assert result["data"]["selected"] == []
    assert result["data"]["missing_required"] == ["read"]
    assert result["data"]["evaluations"] == []
    assert v2.engine.calls == 0


def test_handoff_dedup_revision_role_round_and_no_write(v2):
    wid = work(v2)
    sync(v2, "로그인 원인")
    args = dict(
        work_id=wid,
        expected_work_revision=1,
        query="로그인",
        role="독립 검토",
        round=0,
        mode="read",
    )
    first = call(v2, "handoff_prepare", **args)
    again = call(v2, "handoff_prepare", **args)
    assert first["data"]["handoff_id"] == again["data"]["handoff_id"]
    args["round"] = 1
    assert call(v2, "handoff_prepare", **args)["data"]["handoff_id"] != first["data"]["handoff_id"]
    sync(v2, "로그인 원인 변경")
    args["round"] = 0
    assert call(v2, "handoff_prepare", **args)["data"]["handoff_id"] != first["data"]["handoff_id"]
    args["mode"] = "write"
    assert call(v2, "handoff_prepare", **args)["error"]["code"] == "unsupported"


def test_completion_requires_successful_matching_evidence_and_deletion_invalidates(v2):
    wid = work(v2)
    source = sync(v2, "테스트 결과: 성공")
    refs = [{k: source[k] for k in ("source_id", "revision")}]
    criterion = dict(
        kind="criterion_registered",
        criterion_id="tests",
        description="로그인 회귀 테스트",
        required=True,
        target_revision="commit-1",
        source_refs=[],
    )
    assert record(v2, wid, criterion)["outcome"] == "ok"
    passed = dict(
        kind="criterion_result",
        criterion_id="tests",
        status="passed",
        target_revision="commit-1",
        evidence_ids=[],
        reason="실행 결과 확인",
    )
    assert record(v2, wid, passed)["outcome"] == "error"
    evidence = record(
        v2,
        wid,
        dict(
            kind="evidence_reported",
            claim="검사 성공",
            target_revision="commit-1",
            source_refs=refs,
            observation=dict(
                command="pytest", exit_code=0, result_excerpt="passed", target_hash="sha-1"
            ),
        ),
    )
    eid = evidence["data"]["evidence_id"]
    passed["evidence_ids"] = [eid]
    assert record(v2, wid, passed)["outcome"] == "ok"
    assert (
        record(
            v2,
            wid,
            dict(
                kind="completion_reported",
                summary="완료 보고",
                evidence_ids=[eid],
                open_items=[],
                target_revision="commit-1",
            ),
        )["outcome"]
        == "ok"
    )
    assert v2.work(wid)["completion_coverage"] == "reported_complete"
    assert (
        call(
            v2,
            "data_forget",
            expected_data_revision=v2.store.meta()["data_revision"],
            target={"kind": "source", "source_id": source["source_id"]},
        )["outcome"]
        == "ok"
    )
    assert v2.work(wid)["completion_coverage"] == "incomplete"
    assert "criteria" not in v2.work(wid)


def test_required_judgment_unavailable_is_not_success(v2):
    wid = work(v2)
    sync(v2, "로그인 실패")
    result = call(v2, "context_prepare", work_id=wid, query="로그인", judge_mode="required")
    assert result["outcome"] == "insufficient"


def test_required_judgment_with_unjudged_candidates_is_not_success(v2):
    wid = work(v2)
    for index in range(10):
        sync(v2, f"로그인 실패 사례 {index}: 연결 오류", f"case{index}.md")
    v2.config.engine = {"state": "shadow"}
    v2.engine = Judge(choice="relevant")
    # Isolate unjudged-candidate handling from whole-envelope budget truncation.
    args = {"work_id": wid, "query": "로그인", "budget_bytes": 65536}
    optional = call(v2, "context_prepare", **args)
    assert optional["data"]["judgment"]["status"] == "observed"
    assert optional["data"]["judgment"]["unjudged_candidates"] > 0
    assert optional["outcome"] == "ok"
    required = call(v2, "context_prepare", **args, judge_mode="required")
    assert required["data"]["judgment"]["status"] == "observed"
    assert required["outcome"] == "insufficient"


def test_completion_retires_prior_revision_only_when_explicitly_not_applicable(v2):
    from jev_context.completion import coverage

    wid = work(v2)
    evidence_ids = {}
    for target in ("prior", "current"):
        registered = record(
            v2,
            wid,
            dict(kind="criterion_registered", criterion_id=target, description="회귀 검사",
                 required=True, target_revision=target, source_refs=[]),
        )  # fmt: skip
        assert registered["outcome"] == "ok"
        evidence = record(
            v2,
            wid,
            dict(kind="evidence_reported", claim="검사 성공", target_revision=target,
                 source_refs=[], observation=dict(command="pytest", exit_code=0,
                 result_excerpt="passed", target_hash=target)),
        )  # fmt: skip
        evidence_ids[target] = evidence["data"]["evidence_id"]
        assert record(
            v2,
            wid,
            dict(kind="criterion_result", criterion_id=target, status="passed",
                 target_revision=target, evidence_ids=[evidence_ids[target]], reason="검사 완료"),
        )["outcome"] == "ok"  # fmt: skip

    def report_completion():
        result = record(
            v2,
            wid,
            dict(kind="completion_reported", summary="현재 revision 완료 보고",
                 evidence_ids=[evidence_ids["current"]], open_items=[], target_revision="current"),
        )  # fmt: skip
        assert result["outcome"] == "ok"
        return v2.work(wid)["completion_coverage"]

    assert report_completion() == "incomplete"
    for status in ("failed", "not_applicable"):
        result = record(
            v2,
            wid,
            dict(kind="criterion_result", criterion_id="prior", status=status,
                 target_revision="prior", evidence_ids=[], reason="수정 전 revision 기준을 교체함"),
        )  # fmt: skip
        assert result["outcome"] == "ok"
        assert report_completion() == ("incomplete" if status == "failed" else "reported_complete")
    retired = v2.work(wid)
    assert retired["criteria"]["prior"]["target_revision"] == "prior"
    retired["criteria"]["prior"]["reason"] = ""
    assert coverage(v2, retired, "current") == "incomplete"


def test_judgment_inspection_and_source_deletion(v2):
    wid = work(v2)
    source = sync(v2, "로그인 관련 원문")
    v2.engine = Judge()
    v2.config.engine = {"state": "shadow"}
    result = call(v2, "context_prepare", work_id=wid, query="로그인")
    pid = result["data"]["packet_id"]
    inspected = call(v2, "work_inspect", work_id=wid, view="judgments", packet_id=pid)
    assert inspected["data"]["items"][0]["status"] == "observed"
    call(
        v2,
        "data_forget",
        expected_data_revision=v2.store.meta()["data_revision"],
        target={"kind": "source", "source_id": source["source_id"]},
    )
    assert (
        call(v2, "work_inspect", work_id=wid, view="judgments", packet_id=pid)["outcome"] == "error"
    )


def test_known_failure_is_protected_without_query_match(v2, tmp_path):
    wid = work(v2)
    source = sync(v2, "비밀 원인: 이전 검사가 실패했다.")
    record(
        v2,
        wid,
        dict(
            kind="evidence_reported",
            claim="이전 실패",
            role="failure",
            target_revision="revision-1",
            source_refs=[{k: source[k] for k in ("source_id", "revision")}],
            observation=dict(
                command="pytest", exit_code=1, result_excerpt="failed", target_hash="sha-1"
            ),
        ),
    )
    v2.engine = Judge()
    activate(v2, tmp_path)
    result = call(v2, "context_prepare", work_id=wid, query="로그인")
    assert result["data"]["evidence"][0]["role"] == "required_evidence"
    assert "이전 검사가 실패" in result["data"]["evidence"][0]["text"]


def test_migration_revokes_already_open_old_service(service):
    migrated = Store(replace(service.config, contract_version="2.0"), migrate=True)
    migrated.close()
    result = service.call("workspace_status", {"request_id": "old-running"})
    assert result["error"]["code"] == "policy_denied"


def test_insufficient_small_budget_with_long_request_id(v2):
    wid = work(v2, ["필수 근거 " * 300])
    result = call(
        v2, "context_prepare", request_id="r" * 128, work_id=wid, query="로그인", budget_bytes=1024
    )
    assert result["outcome"] == "insufficient"
    assert measure(result) <= 1024
