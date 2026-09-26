from dataclasses import replace

from conftest import call, record, sync, work
from jsonschema import Draft202012Validator

from jev_context.common import dumps
from jev_context.service import CONTRACT, Service


def test_default_scope_is_normalized_for_replay(service):
    create = dict(
        title="x", goal="x", scope=dict(mode="design", constraints=[]), origin=dict(quote="x")
    )
    a = call(service, "work_open", mutation_id="default", create=create)
    create["scope"]["allowed_actions"] = ["read", "write_design"]
    b = call(service, "work_open", mutation_id="default", create=create)
    assert a["data"] == b["data"]


def test_goal_change_preserves_history(service):
    wid = work(service)
    result = record(
        service,
        wid,
        dict(
            kind="scope_revised",
            goal="구현 완료",
            scope=dict(
                mode="implement",
                allowed_actions=["read", "edit_code", "run_checks"],
                constraints=[],
            ),
            origin=dict(quote="이제 구현해줘"),
            reason="최신 사용자 요청",
        ),
    )
    assert result["outcome"] == "ok"
    restored = call(service, "work_open", work_id=wid)["data"]
    assert restored["goal"] == "구현 완료"
    history = call(service, "work_inspect", work_id=wid, view="events")["data"]["items"]
    assert history[0]["create"]["goal"] == "문서 완성"


def test_policy_revision_invalidates_older_process(service):
    sync(service)
    updated = replace(service.config, allowed_paths=("none/*.md",), policy_revision=2)
    other = Service(updated)
    try:
        assert call(service, "workspace_status")["error"]["code"] == "policy_denied"
        assert call(other, "workspace_status")["outcome"] == "ok"
    finally:
        other.close()


def test_missing_source_is_reported_in_coverage(service):
    item = sync(service)
    (service.config.project_root / "rules.md").unlink()
    result = call(service, "context_prepare", work_id=work(service), query="권한")
    assert result["outcome"] == "partial"
    assert result["data"]["coverage"]["source_observations"][item["source_id"]] == "missing"


def test_canonical_output_and_byte_limits(service):
    wid = work(service)
    sync(service)
    validator = Draft202012Validator(CONTRACT["output"])
    for budget in (1, 512, 16384):
        result = call(service, "context_prepare", work_id=wid, query="권한", budget_bytes=budget)
        validator.validate(result)
        body = result["data"]
        if result["outcome"] != "insufficient":
            assert (
                len(dumps({k: body[k] for k in ("scope", "protected", "evidence")}).encode())
                == body["budget"]["used_bytes"]
            )
        assert body["budget"]["used_bytes"] <= budget


def test_shadow_never_discards_evidence_and_changed_sources_cancel_judgment(service):
    sync(service, "권한: 승인 후 배포")
    wid = work(service)
    service.config.engine = {"state": "shadow"}

    class Engine:
        state = "shadow"
        fingerprint = "fixture-fingerprint"
        change = False

        def evaluate(self, request):
            if self.change:
                (service.config.project_root / "rules.md").write_text(
                    "권한: 배포 금지", encoding="utf-8"
                )
            return dict(
                status="observed",
                evaluation_id=request["evaluation_id"],
                answers=[{"choice": "insufficient_evidence"}],
            )

    engine = Engine()
    service.engine = engine
    observed = call(service, "context_prepare", work_id=wid, query="권한")
    assert observed["data"]["judgment"]["status"] == "observed"
    assert observed["data"]["evidence"]
    engine.change = True
    changed = call(service, "context_prepare", work_id=wid, query="권한")
    assert changed["outcome"] == "partial"
    assert changed["data"]["judgment"]["status"] == "abstained"
    assert "evaluation" not in changed["data"]["judgment"]


def test_unpaired_unicode_is_rejected(service):
    result = call(
        service,
        "source_sync",
        items=[dict(kind="excerpt", text="\ud800", origin_label="x", external_key="x")],
    )
    assert result["error"]["code"] == "invalid_argument"
