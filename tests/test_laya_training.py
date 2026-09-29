import json
import sqlite3
import struct
from types import SimpleNamespace

import pytest

from jev_context.laya_worker import validate_rotary_config
from scripts import prepare_laya_training_data as data
from scripts.train_laya_pilot import compatible_encoder_config, safetensors_header


def candidate(name, work, source, text):
    row = {
        "id": name,
        "work_id": work,
        "source_refs": [{"source_id": source, "locator": source}],
        "state": {"query": "query", "candidate": text},
        "purpose": "relevance",
        "expected": "relevant",
        "critical": False,
        "review": {
            "status": "human_reviewed",
            "reviewer": "tester",
            "reviewed_at": "2026-09-28",
            "reason": "Fixture only",
        },
        "provenance": "captured_model_request",
    }
    row["state_sha256"] = data.digest(row["state"])
    row["source_record"] = {"request": {"state": row["state"]}}
    row["source_record_sha256"] = data.digest(row["source_record"])
    return row


def test_pair_parent_cannot_cross_groups_without_shared_work_or_source():
    rows = [candidate("a", "w1", "s1", "one"), candidate("b", "w2", "s2", "two")]
    rows[1]["derived_from"] = "a"
    assert len(set(data.assign_groups(rows))) == 1


def test_source_id_links_rows_even_when_only_one_has_a_locator():
    rows = [candidate("a", "w1", "s", "one"), candidate("b", "w2", "s", "two")]
    rows[0]["source_refs"][0]["locator"] = "docs/example.md"
    rows[1]["source_refs"][0].pop("locator")
    assert len(set(data.assign_groups(rows))) == 1


def test_request_snapshot_is_exact_and_expired_request_is_not_called(monkeypatch):
    from jev_context import judgment

    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(judgment.time, "monotonic", lambda: next(ticks))
    calls = []

    def evaluate(request):
        calls.append(request["evaluation_id"])
        request["state"]["query"] = "engine changed its input"
        return {"status": "observed", "answers": []}

    service = SimpleNamespace(
        engine=SimpleNamespace(fingerprint="test", evaluate=evaluate),
        config=SimpleNamespace(engine={"state": "shadow"}, project_id="fixture"),
    )
    entries = [
        ({"query": "original", "candidate": "one"}, ["relevance"]),
        ({"query": "second", "candidate": "two"}, ["evidence_relation"]),
    ]
    rows = judgment.evaluate_many(service, entries, deadline=1.0)
    assert len(calls) == 1
    assert rows[0]["request"]["state"]["query"] == "original"
    assert rows[0]["request_dispatched"] is True
    assert rows[1]["request_dispatched"] is False


def test_development_exposure_blocks_entire_heldout_group():
    for index in range(100):
        row = candidate(str(index), str(index), str(index), str(index))
        group = data.assign_groups([row])[0]
        if int(group[:8], 16) % 100 >= 90:
            break
    else:
        pytest.fail("No test group in deterministic fixture search")
    row["eligible_for_independent_test"] = False
    report, prepared = data.prepare_reviewed([row], minimum=1)
    assert not prepared
    assert any("Development-exposed group" in e for e in report["errors"])


def test_review_only_case_and_changed_input_are_rejected_even_with_human_flag():
    row = candidate("a", "w", "s", "one")
    row["usage"] = "rubric_review_only"
    row["state"]["query"] = "edited"
    report, prepared = data.prepare_reviewed([row], minimum=1)
    assert not prepared
    assert any("Review-only" in e for e in report["errors"])
    assert any("Original input" in e for e in report["errors"])


def test_record_audit_preserves_requests_and_does_not_invent_old_inputs(tmp_path):
    path = tmp_path / "records.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE packets(id TEXT,work_id TEXT,body TEXT)")
    db.execute("CREATE TABLE evidence(id TEXT,work_id TEXT,body TEXT)")
    db.execute("CREATE TABLE source_refs(owner_type TEXT,owner_id TEXT,locator TEXT)")
    db.execute("CREATE TABLE works(id TEXT,body TEXT)")
    state = {"query": "로그인", "candidate": "실제 기록"}
    result = {
        "request": {"state": state, "questions": [{"purpose": "relevance"}]},
        "input_hash": data.digest(state),
        "request_dispatched": True,
        "answers": [{"choice": "relevant"}],
    }
    packet = {"judgment": {"evaluations": [result, {"input_hash": "legacy"}]}}
    db.execute("INSERT INTO packets VALUES(?,?,?)", ("p", "w", json.dumps(packet)))
    evidence = {
        "claim": "검사 통과",
        "observation": {"command": "pytest", "exit_code": 0, "result_excerpt": "1 passed"},
        "provenance": "agent_reported",
    }
    db.execute("INSERT INTO evidence VALUES(?,?,?)", ("e", "w", json.dumps(evidence)))
    db.execute(
        "INSERT INTO works VALUES(?,?)",
        ("verification", json.dumps({"scope": {"constraints": [data.VERIFICATION_MARKER]}})),
    )
    db.execute("INSERT INTO packets VALUES(?,?,?)", ("vp", "verification", json.dumps(packet)))
    db.execute("INSERT INTO evidence VALUES(?,?,?)", ("ve", "verification", json.dumps(evidence)))
    marked = {**packet, "scope": {"constraints": [data.VERIFICATION_MARKER]}}
    db.execute("INSERT INTO packets VALUES(?,?,?)", ("marked", "other", json.dumps(marked)))
    db.commit()
    db.close()
    original = path.read_bytes()
    rows, report = data.audit_records(path)
    assert len(rows) == 2 and report["evaluations_without_original_request"] == 1
    assert report["verification_packets_excluded"] == 2
    assert report["verification_evaluations_excluded"] == 4
    assert report["verification_evidence_excluded"] == 1
    assert rows[0]["state"] == state and rows[0]["original_model_request"]
    assert rows[1]["source_record"] == evidence and not rows[1]["original_model_request"]
    assert all(r["expected"] is None and r["review"]["status"] == "pending" for r in rows)
    assert path.read_bytes() == original


@pytest.mark.parametrize("field", ["constraints", "scope"])
def test_verification_request_cannot_be_made_training_data_by_human_flag(field):
    row = candidate("test", "w", "s", "verification")
    row["state"][field] = (
        [data.VERIFICATION_MARKER]
        if field == "constraints"
        else {"constraints": [data.VERIFICATION_MARKER]}
    )
    row["state_sha256"] = data.digest(row["state"])
    report, prepared = data.prepare_reviewed([row], minimum=1)
    assert not prepared and any("Review-only" in e for e in report["errors"])


def test_groups_keep_transitive_work_and_source_overlap_together():
    rows = [
        candidate("a", "work1", "doc1", "first"),
        candidate("b", "work1", "doc2", "second"),
        candidate("c", "work2", "doc2", "third"),
        candidate("d", "work3", "doc3", "fourth"),
    ]
    groups = data.assign_groups(rows)
    assert groups[0] == groups[1] == groups[2]
    assert groups[3] != groups[0]
    assert groups == data.assign_groups(rows)


def test_reaudit_keeps_exposure_but_rejects_changed_input():
    original = candidate("a", "w", "s", "original")
    original["eligible_for_independent_test"] = False
    original["agent_review"] = {"draft_label": "relevant", "human_reviewed": False}
    fresh = candidate("a", "w", "s", "original")
    assert data.retain_reviews([fresh], [original]) == 1
    assert fresh["eligible_for_independent_test"] is False
    assert fresh["agent_review"] == original["agent_review"]
    original["state"]["query"] = "changed"
    with pytest.raises(ValueError, match="state changed"):
        data.retain_reviews([fresh], [original])


def test_identical_source_text_cannot_cross_groups_even_when_ids_differ():
    rows = [candidate("a", "work1", "doc1", "same"), candidate("b", "work2", "doc2", "same")]
    assert len(set(data.assign_groups(rows))) == 1


def test_shared_raw_result_cannot_cross_groups():
    rows = [candidate("a", "w1", "s1", "one"), candidate("b", "w2", "s2", "two")]
    for row in rows:
        row["automatic_checks"] = {
            "raw_observation_check": {
                "files": [{"path": "local/result.json", "sha256": "same-run"}]
            }
        }
    assert len(set(data.assign_groups(rows))) == 1


def test_model_prediction_is_not_a_human_gold_label():
    row = candidate("a", "work1", "doc1", "first")
    row.update(expected=None, model_prediction="relevant", review={"status": "pending"})
    report, prepared = data.prepare_reviewed([row], minimum=1)
    assert report["status"] == "not_ready"
    assert any("Verified review" in e for e in report["errors"])
    assert any("Gold label" in e for e in report["errors"])
    assert not prepared


def agent_verified_candidate():
    row = candidate("a", "work1", "doc1", "first")
    evidence = [{"path": "result.json", "sha256": "a" * 64}]
    row["automatic_checks"] = {"raw_observation_check": {"files": evidence}}
    row["review"].update(
        status="agent_verified",
        reviewer="Codex",
        reviewer_type="agent",
        target_model_output_used_as_label=False,
        checked_state_sha256=row["state_sha256"],
        checked_record_sha256=row["source_record_sha256"],
        checked_label=row["expected"],
        checked_critical=row["critical"],
        evidence=evidence,
    )
    return row


def test_grounded_agent_review_is_accepted_without_claiming_human_review():
    row = agent_verified_candidate()
    assert data.verified_review(row)
    report, prepared = data.prepare_reviewed([row], minimum=1)
    assert not any("Verified review" in e for e in report["errors"])
    assert report["review_status_counts"] == {"agent_verified": 1}
    assert not prepared  # Other purposes and independent splits are still required.


@pytest.mark.parametrize(
    "change", ["label", "state", "record", "prediction", "evidence", "draft", "incomplete"]
)
def test_agent_review_rejects_unverified_or_changed_answers(change):
    row = agent_verified_candidate()
    if change == "label":
        row["expected"] = "irrelevant"
    elif change == "state":
        row["state"]["query"] = "changed"
    elif change == "record":
        row["source_record"]["new"] = "changed"
    elif change == "prediction":
        row["review"]["target_model_output_used_as_label"] = True
    elif change == "evidence":
        row["review"]["evidence"] = []
    elif change == "incomplete":
        row["critical"] = row["review"]["checked_critical"] = None
    else:
        row["review"]["status"] = "agent_reviewed"
    assert not data.verified_review(row)


def test_previously_exposed_case_is_rejected_even_with_review_flag():
    row = candidate("a", "work1", "doc1", "first")
    row["state"] = json.loads(
        (data.ROOT / "evaluations/ollaya-tuning/validation.json").read_text(encoding="utf-8")
    )["cases"][0]["state"]
    report, prepared = data.prepare_reviewed([row], minimum=1)
    assert any("Previously exposed" in e for e in report["errors"])
    assert not prepared


def test_single_connected_group_cannot_be_claimed_as_four_independent_sets():
    row = candidate("a", "work1", "doc1", "first")
    report, prepared = data.prepare_reviewed([row], minimum=1)
    assert report["status"] == "not_ready"
    assert any("No independent group" in e for e in report["errors"])
    assert not prepared


def test_inventory_is_reconstructed_and_never_turns_a_prediction_into_gold(tmp_path):
    path = tmp_path / "state.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE packets(id TEXT,work_id TEXT,body TEXT)")
    packet = {
        "scope": {"goal": "find memory policy"},
        "judgment": {"evaluations": [{"answers": [{"choice": "relevant"}]}]},
        "evidence": [{"source_id": "doc", "revision": "r1", "text": "source text"}],
    }
    db.execute("INSERT INTO packets VALUES(?,?,?)", ("packet", "work", json.dumps(packet)))
    db.commit()
    db.close()
    original = path.read_bytes()
    rows, report = data.inventory(path)
    assert len(rows) == 1 and report["evaluations_without_original_state"] == 1
    assert rows[0]["expected"] is None
    assert rows[0]["original_model_request"] is False
    assert path.read_bytes() == original


def test_review_html_escapes_script_closing_text(tmp_path):
    path = tmp_path / "review.html"
    row = candidate("a", "work", "doc", "</script><script>malicious()</script>")
    data.write_review_html(path, [row])
    text = path.read_text(encoding="utf-8")
    assert "</script><script>malicious()" not in text
    assert "\\u003c/script>" in text


def test_legacy_rotary_translation_preserves_full_and_local_theta():
    original = {
        "num_hidden_layers": 4,
        "global_attn_every_n_layers": 3,
        "rope_parameters": {
            key: {"rope_type": "default", "rope_theta": 160000}
            for key in ("full_attention", "sliding_attention")
        },
    }
    translated, changes = compatible_encoder_config(original)
    assert translated["local_rope_theta"] == translated["global_rope_theta"] == 160000
    assert changes["local_rope_theta"] == 160000
    assert "local_rope_theta" not in original
    with pytest.raises(ValueError, match="Conflicting"):
        compatible_encoder_config({**original, "local_rope_theta": 10000})


def test_safetensors_header_rejects_unbounded_length(tmp_path):
    path = tmp_path / "weights"
    path.write_bytes(struct.pack("<Q", 2**63))
    with pytest.raises(ValueError, match="header size"):
        safetensors_header(path)


def test_runtime_rejects_silently_ignored_checkpoint_rotary_parameters():
    checkpoint = {"rope_parameters": {"sliding_attention": {"rope_theta": 160000}}}
    with pytest.raises(ValueError, match="incompatible"):
        validate_rotary_config(checkpoint, SimpleNamespace(local_rope_theta=10000))
    validate_rotary_config(checkpoint, SimpleNamespace(local_rope_theta=160000))
    validate_rotary_config(
        checkpoint, SimpleNamespace(rope_parameters=checkpoint["rope_parameters"])
    )
