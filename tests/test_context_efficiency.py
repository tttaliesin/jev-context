import json

from test_v2 import Judge, call, record, sync, work
from test_v2 import v2 as v2

from jev_context.budget import measure
from jev_context.common import dumps


def prepare(service, wid, **kwargs):
    return call(service, "context_prepare", work_id=wid, query="로그인", judge_mode="off", **kwargs)


def test_legacy_completion_projection_does_not_rewrite_history(v2):
    wid = work(v2)
    old_progress = {"summary": "실행 중", "next_actions": ["이미 완료된 검사 실행"]}
    record(v2, wid, {"kind": "progress_reported", "source_refs": [], **old_progress})
    # Simulate a persisted work completed by the previous release.
    body = v2.work(wid)
    body.update(
        status="completion_reported", completion_summary="검사 완료", open_items=["독립 평가"]
    )
    v2.store.put_body("works", wid, body)
    before = dumps(v2.work(wid))
    opened = call(v2, "work_open", work_id=wid)["data"]
    assert opened["progress"] == {"summary": "검사 완료", "next_actions": []}
    assert opened["checkpoint"]["open_items"] == ["독립 평가"]
    assert prepare(v2, wid)["data"]["scope"]["checkpoint"]["next_actions"] == []
    assert dumps(v2.work(wid)) == before
    events = call(v2, "work_inspect", work_id=wid, view="events")["data"]["items"]
    assert any(e.get("next_actions") == old_progress["next_actions"] for e in events)


def test_completion_and_reopening_have_current_progress(v2):
    wid = work(v2)
    record(
        v2,
        wid,
        {
            "kind": "progress_reported",
            "summary": "진행",
            "next_actions": ["검사"],
            "source_refs": [],
        },
    )
    assert (
        record(
            v2,
            wid,
            {
                "kind": "completion_reported",
                "summary": "완료",
                "evidence_ids": [],
                "open_items": [],
                "target_revision": "v1",
            },
        )["outcome"]
        == "ok"
    )
    assert v2.work(wid)["progress"]["next_actions"] == []
    assert (
        record(
            v2,
            wid,
            {"kind": "work_reopened", "reason": "추가 요구 반영", "origin": {"quote": "수정해줘"}},
        )["outcome"]
        == "ok"
    )
    current = call(v2, "work_open", work_id=wid)["data"]["checkpoint"]
    assert current == {"status": "active", "summary": "추가 요구 반영", "next_actions": []}


def test_retired_criteria_and_failure_details_remain_inspectable(v2):
    wid = work(v2, ["승인 없이는 삭제 금지"])
    sync(v2, "로그인 관련 현재 근거")
    for index in range(8):
        cid = f"old-{index}"
        record(
            v2,
            wid,
            {
                "kind": "criterion_registered",
                "criterion_id": cid,
                "description": "과거 검사 설명 " * 80,
                "required": True,
                "target_revision": "v0",
                "source_refs": [],
            },
        )
        record(
            v2,
            wid,
            {
                "kind": "criterion_result",
                "criterion_id": cid,
                "status": "not_applicable",
                "target_revision": "v0",
                "evidence_ids": [],
                "reason": "현재 기준으로 대체",
            },
        )
    observation = {
        "command": "pytest",
        "exit_code": 1,
        "target_hash": "abc",
        "result_excerpt": "실패 원본 로그 " * 300,
    }
    failure = record(
        v2,
        wid,
        {
            "kind": "evidence_reported",
            "claim": "로그인 예외가 재현됨",
            "role": "failure",
            "target_revision": "v0",
            "observation": observation,
            "source_refs": [],
        },
    )
    assert failure["outcome"] == "ok"
    packet = prepare(v2, wid, budget_bytes=16384)["data"]
    assert packet["evidence"]
    protected = packet["protected"]
    assert any(p.get("text") == "승인 없이는 삭제 금지" for p in protected)
    assert any(
        p.get("claim") == "로그인 예외가 재현됨" and p["observation"]["exit_code"] == 1
        for p in protected
    )
    criteria = call(v2, "work_inspect", work_id=wid, view="criteria")["data"]["items"]
    assert len(criteria) == 8 and all(len(c["description"]) > 500 for c in criteria)
    evidence = call(v2, "work_inspect", work_id=wid, view="evidence")["data"]["items"]
    assert evidence[0]["observation"] == observation


def test_required_full_source_deduplicates_subchunks_but_preserves_other_revision(v2):
    wid = work(v2)
    text = "# 로그인 정책\n" + "로그인에는 승인 필요\n" * 180
    old = sync(v2, text)
    refs = [{k: old[k] for k in ("source_id", "revision")}]
    packet = prepare(v2, wid, required_refs=refs, budget_bytes=65536)["data"]
    assert len(packet["evidence"]) == 1
    assert (
        packet["evidence"][0]["text"] == (v2.config.project_root / "rules.md").read_bytes().decode()
    )
    sync(v2, "# 로그인 정책\n승인 정책 변경. 과거 허용은 현재 허용이 아님.")
    evidence = prepare(v2, wid, required_refs=refs, budget_bytes=65536)["data"]["evidence"]
    assert len(evidence) == 2
    assert evidence[0]["freshness"] == "historical"
    assert evidence[1]["freshness"] == "verified_at_read"
    assert evidence[1]["locator"] == "rules.md"


def test_fragments_of_one_long_line_are_not_duplicates(v2):
    wid = work(v2)
    text = "로그인 " * 1800
    sync(v2, text)
    evidence = prepare(v2, wid, budget_bytes=65536)["data"]["evidence"]
    assert len(evidence) > 1
    assert "".join(e["text"] for e in sorted(evidence, key=lambda x: x["byte_start"])) == text


def test_wire_budget_omission_is_partial_and_body_size_is_current(v2):
    wid = work(v2)
    sync(v2, "로그인 " * 600)
    result = prepare(v2, wid, budget_bytes=5000)
    assert result["outcome"] == "partial", result
    data = result["data"]
    assert data["coverage"]["budget_omitted_candidates"] > 0
    assert data["coverage"]["retrieval_status"] == "budget_limited"
    assert not data["evidence"]
    assert measure(result) <= 5000
    assert data["wire_budget"]["used_bytes"] == measure(result)
    assert data["budget"]["used_bytes"] == len(
        dumps({k: data[k] for k in ("scope", "protected", "evidence")}).encode()
    )


def test_shadow_details_are_inspectable_and_read_only_keeps_inline_details(v2):
    wid = work(v2)
    sync(v2, "로그인 정책")
    v2.config.engine = {"state": "shadow"}
    v2.engine = Judge()
    result = call(v2, "context_prepare", work_id=wid, query="로그인", judge_mode="required")
    data = result["data"]
    assert "evaluations" not in data["judgment"]
    assert data["judgment"]["evaluation_count"] == 1
    details = call(v2, "work_inspect", work_id=wid, view="judgments", packet_id=data["packet_id"])
    assert len(details["data"]["items"]) == 1
    assert data["evidence"]  # shadow never discards evidence
    v2.config.read_only = True
    readonly = call(v2, "context_prepare", work_id=wid, query="로그인", judge_mode="required")[
        "data"
    ]
    assert readonly["judgment"]["evaluations"]
    assert "inspection" not in readonly["judgment"]


def test_off_never_invokes_model_and_preserves_negative_constraint_and_conflict(v2):
    wid = work(v2, ["모델 실행 금지. 단, 명시적으로 요청한 평가만 허용."])
    v2.engine = Judge()
    sync(v2, "로그인 실패: 현재 정책은 승인 없이 삭제하면 안 된다.")
    record(
        v2,
        wid,
        {
            "kind": "issue_opened",
            "issue_kind": "conflict",
            "text": "과거 허용과 현재 금지 정책이 충돌함",
            "source_refs": [],
        },
    )
    result = prepare(v2, wid)
    assert v2.engine.calls == 0
    assert "명시적으로" in dumps(result)
    assert any(
        p.get("text") == "과거 허용과 현재 금지 정책이 충돌함" for p in result["data"]["protected"]
    )
    empty = call(v2, "context_prepare", work_id=wid, query="존재하지않는용어", judge_mode="off")[
        "data"
    ]
    assert empty["coverage"]["retrieval_status"] == "no_evidence"
    assert not empty["evidence"]
    assert json.loads(dumps(empty))["scope"]["goal"]
