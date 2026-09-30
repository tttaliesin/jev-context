import copy
import json
import sqlite3
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_context.laya_worker import validate_rotary_config
from scripts import prepare_laya_training_data as data
from scripts.train_laya_pilot import compatible_encoder_config, safetensors_header


def reservation_fixture(tmp_path):
    path = tmp_path / "reservation.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE works(id TEXT,body TEXT);
        CREATE TABLE packets(id TEXT,work_id TEXT,body TEXT);
        CREATE TABLE evidence(id TEXT,work_id TEXT,body TEXT);
        CREATE TABLE sources(id TEXT,locator TEXT,current_revision TEXT,status TEXT);
        CREATE TABLE source_revisions(id TEXT,source_id TEXT,sha256 TEXT);
        CREATE TABLE source_refs(source_id TEXT,revision TEXT);
    """)
    db.execute("INSERT INTO works VALUES('new-work','{}')")
    db.execute("INSERT INTO sources VALUES('new-source','docs/new','rev-new','available')")
    db.execute("INSERT INTO source_revisions VALUES('rev-new','new-source',?)", ("a" * 64,))
    db.commit()
    db.close()
    spec = [{"id": "holdout-1", "work_id": "new-work", "source_ids": ["new-source"]}]
    return path, spec


def reserved_row(manifest):
    row = candidate("new-case", "new-work", "new-source", "new input")
    row["source_refs"][0].update(locator="docs/new", revision="rev-new")
    row["source_record"].update(
        captured_at="2099-01-01T00:00:00Z",
        source_ref=row["source_refs"][0],
        answers=["HIDDEN-PREDICTION"],
    )
    row["source_record_sha256"] = data.digest(row["source_record"])
    row.update(exposure="development_audit", eligible_for_independent_test=False)
    data.apply_reservations([row], manifest, [])
    return row


def test_reservation_is_readonly_blinds_prediction_and_preserves_record(tmp_path):
    path, spec = reservation_fixture(tmp_path)
    original = path.read_bytes()
    manifest = data.reserve_test(path, spec, [])
    assert path.read_bytes() == original
    row = reserved_row(manifest)
    assert row["exposure"] == "test_reserved"
    assert data.reservation_for(row, manifest) == "holdout-1"
    blinded = data.blind_review_rows([row])[0]
    assert "HIDDEN-PREDICTION" not in json.dumps(blinded)
    assert "source_record" not in blinded
    html = tmp_path / "review.html"
    data.write_review_html(html, [row])
    assert "HIDDEN-PREDICTION" not in html.read_text("utf-8")
    assert row["source_record"]["answers"] == ["HIDDEN-PREDICTION"]
    blinded.update(expected="irrelevant", critical=True, review={"status": "pending"})
    data.merge_decisions([row], [blinded])
    assert row["expected"] == "irrelevant"
    blinded["state"] = {"candidate": "edited"}
    with pytest.raises(ValueError, match="changed"):
        data.merge_decisions([row], [blinded])


@pytest.mark.parametrize("existing", ["packet", "source", "history", "verification", "duplicate"])
def test_cannot_reserve_exposed_or_verification_sources(tmp_path, existing):
    path, spec = reservation_fixture(tmp_path)
    db = sqlite3.connect(path)
    history = []
    if existing == "packet":
        db.execute("INSERT INTO packets VALUES('p','new-work','{}')")
    elif existing == "source":
        db.execute("INSERT INTO source_refs VALUES('new-source','rev-new')")
    elif existing == "duplicate":
        db.execute("INSERT INTO source_revisions VALUES('rev-old','old-source',?)", ("a" * 64,))
        db.execute("INSERT INTO source_refs VALUES('old-source','rev-old')")
    elif existing == "verification":
        db.execute(
            "UPDATE works SET body=?",
            (json.dumps({"scope": {"constraints": [data.VERIFICATION_MARKER]}}),),
        )
    else:
        old = candidate("old", "old-work", "old-source", "old content")
        old["source_refs"][0]["locator"] = "docs/new"
        history = [old]
    db.commit()
    db.close()
    with pytest.raises(ValueError):
        data.reserve_test(path, spec, history)


def test_reservation_cannot_promote_existing_development_or_cross_groups(tmp_path):
    path, spec = reservation_fixture(tmp_path)
    manifest = data.reserve_test(path, spec, [])
    row = reserved_row(manifest)
    older = copy.deepcopy(row)
    older.update(exposure="development_audit", eligible_for_independent_test=False)
    data.apply_reservations([row], manifest, [], [older])
    assert row["eligible_for_independent_test"] is False
    row = reserved_row(manifest)
    other = candidate("derived", "other-work", "other-source", "different input")
    other["derived_from"] = row["id"]
    data.apply_reservations([row, other], manifest, [])
    assert row["exposure"] == "reservation_invalidated"
    assert row["eligible_for_independent_test"] is False


def test_reservation_requires_time_hash_and_label_group_replication(tmp_path):
    path, spec = reservation_fixture(tmp_path)
    manifest = data.reserve_test(path, spec, [])
    row = reserved_row(manifest)
    report, frozen = data.prepare_reviewed([row], minimum=1, reservations=manifest)
    assert not frozen
    assert any("independent replication" in e for e in report["errors"])
    row["source_record"]["captured_at"] = "2020-01-01T00:00:00Z"
    assert data.reservation_for(row, manifest) is None
    manifest["reserved_at"] = "2010-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="hash"):
        data.reservation_for(row, manifest)


def test_frozen_hashes_split_selection_and_no_overwrite(tmp_path):
    questions = {
        p: {"type": "choice", "criteria": {label: label for label in labels}}
        for p, labels in data.LABELS.items()
    }
    rows = []
    for split in ("train", "development", "calibration", "test"):
        for purpose in data.LABELS:
            identity = split + purpose
            row = candidate(identity, identity, identity, identity)
            row.update(purpose=purpose, split=split, group_id=identity)
            rows.append(row)
    report = data.freeze_dataset(rows, {"status": "ready"}, questions, tmp_path)
    manifest = tmp_path / "prepare.json"
    manifest.write_text(json.dumps(report), encoding="utf-8")
    target = tmp_path / "frozen.jsonl"
    selected = data.load_frozen(target, manifest, "train")
    assert len(selected) == 3 and {r["split"] for r in selected} == {"train"}
    with pytest.raises(FileExistsError):
        data.freeze_dataset(rows, {"status": "ready"}, questions, tmp_path)
    report["split_sha256"] = "changed"
    manifest.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        data.load_frozen(target, manifest, "train")
    target.write_text(target.read_text("utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        data.load_frozen(target, manifest, "train")


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


def test_capability_reservation_binds_exact_inventory_item_and_blocks_reused_tool(tmp_path):
    path, spec = reservation_fixture(tmp_path)
    item = {"id": "new-tool", "version": "1", "description": "Read files", "available": True}
    spec[0]["source_ids"] = []
    spec[0]["capability_sources"] = [
        {"id": item["id"], "version": item["version"], "sha256": data.digest(item)}
    ]
    manifest = data.reserve_test(path, spec, [])
    row = candidate("cap", "new-work", "packet", item)
    row.update(
        purpose="capability_fit", exposure="development_audit", eligible_for_independent_test=False
    )
    row["source_record"]["captured_at"] = "2099-01-01T00:00:00Z"
    data.apply_reservations([row], manifest, [])
    assert row["exposure"] == "test_reserved"
    row["state"]["candidate"] = {**item, "description": "Changed"}
    assert data.reservation_for(row, manifest) is None
    with pytest.raises(ValueError, match="already exposed"):
        data.reserve_test(path, spec, [row])
    older = candidate("other", "old-work", "old-packet", {**item, "description": "Old"})
    older["purpose"] = "capability_fit"
    assert len(set(data.assign_groups([row, older]))) == 1


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
    assert report["split_purpose_counts"].get("test/relevance", 0) == 0
    assert any("Need 30 heldout" in e for e in report["errors"])


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


def synthetic_fixture():
    import json
    from pathlib import Path

    return json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "evaluations/laya-finetuning/synthetic-20260929.json"
        ).read_text("utf-8")
    )


def test_frozen_evaluator_busy_does_not_unload_other_model(tmp_path, monkeypatch):
    import argparse
    import json

    import pytest

    from scripts import evaluate_ollaya as evaluate
    from scripts.prepare_laya_training_data import freeze_dataset, prepare_synthetic

    fixture = synthetic_fixture()
    report, rows = prepare_synthetic(fixture["cases"], fixture["reservation"])
    report = freeze_dataset(rows, report, fixture["questions"], tmp_path)
    manifest = tmp_path / "prepare.json"
    manifest.write_text(json.dumps(report), "utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"lock_root": str(tmp_path / "resident")}), "utf-8")
    calls = []

    class Client:
        def __init__(self, endpoint):
            pass

        def call(self, path):
            calls.append(path)
            return {"models": ["someone-elses-model"]}

        def unload(self, model):
            pytest.fail("Must not unload another model on a busy result")

    monkeypatch.setattr(evaluate, "Client", Client)
    monkeypatch.setattr(evaluate, "ROOT", tmp_path)
    candidate_lock = tmp_path / "candidate-lock.json"
    candidate_lock.write_text("{}")
    monkeypatch.setattr(evaluate, "check_candidate_lock", lambda *args: {})
    args = argparse.Namespace(
        dataset=tmp_path / "frozen.jsonl",
        manifest=manifest,
        split="test",
        data_profile="synthetic-experiment",
        model="laya:multilingual",
        backend="ollaya",
        candidate_lock=candidate_lock,
        endpoint="unused",
        profile=profile,
        output=tmp_path / "results",
    )
    with pytest.raises(SystemExit):
        evaluate.frozen_evaluation(args)
    saved = json.loads((args.output / "test.json").read_text("utf-8"))
    assert len(saved["rows"]) == 270
    assert all(r["status"] == "not_run" for r in saved["rows"])
    assert calls == ["/api/ps"]
    args.model = "jev-laya:pilot"
    args.candidate_lock = None
    with pytest.raises(ValueError, match="frozen candidate"):
        evaluate.frozen_evaluation(args)


def test_candidate_lock_rejects_changed_checkpoint(tmp_path):
    import json

    import pytest

    from scripts.evaluate_ollaya import check_candidate_lock, sha

    dataset = tmp_path / "frozen.jsonl"
    checkpoint = tmp_path / "model.safetensors"
    dataset.write_text("frozen", "utf-8")
    checkpoint.write_text("before", "utf-8")
    lock = tmp_path / "candidate-lock.json"
    lock.write_text(
        json.dumps(
            {
                "dataset_sha256": sha(dataset),
                "files": [{"path": str(checkpoint), "sha256": sha(checkpoint)}],
            }
        ),
        "utf-8",
    )
    assert check_candidate_lock(lock, dataset)
    checkpoint.write_text("after", "utf-8")
    with pytest.raises(ValueError, match="changed"):
        check_candidate_lock(lock, dataset)


def test_process_memory_is_positive_and_missing_pid_is_not_zero():
    import os

    import pytest

    from scripts.evaluate_ollaya import process_snapshot

    if os.name != "nt":
        pytest.skip("Windows process memory sampler")
    observed = process_snapshot(os.getpid())
    assert observed["working_set_sum_bytes"] > 0
    assert os.getpid() in {p["ProcessId"] for p in observed["processes"]}
    with pytest.raises(RuntimeError, match="unavailable"):
        process_snapshot(0xFFFFFFFE)


def test_synthetic_profile_balanced_and_actual_gate_unchanged():
    from scripts.prepare_laya_training_data import prepare_reviewed, prepare_synthetic

    fixture = synthetic_fixture()
    report, rows = prepare_synthetic(fixture["cases"], fixture["reservation"])
    assert report["status"] == "ready"
    assert report["groups"] == 30 and len(rows) == 270
    actual, _ = prepare_reviewed(rows)
    assert actual["status"] == "not_ready"
    assert any("Need 600" in error for error in actual["errors"])


def test_synthetic_review_tampering_and_family_leak_rejected():
    import copy

    from scripts.prepare_laya_training_data import prepare_synthetic

    fixture = synthetic_fixture()
    changes = [
        lambda r: r[0].update(expected="irrelevant"),
        lambda r: r[0]["state"].update(candidate="changed"),
        lambda r: r[0]["review"].update(target_model_output_used_as_label=True),
        lambda r: r[0].update(provenance="captured_model_request"),
        lambda r: r[0].update(usage="verification_only"),
        lambda r: r[-1].update(pair_group_id=r[0]["pair_group_id"]),
        lambda r: r[0]["review"].update(human_reviewed=True),
    ]
    for change in changes:
        rows = copy.deepcopy(fixture["cases"])
        change(rows)
        assert prepare_synthetic(rows, fixture["reservation"])[0]["status"] == "not_ready"
    assert (
        prepare_synthetic(fixture["cases"], fixture["reservation"], [fixture["cases"][0]])[0][
            "status"
        ]
        == "not_ready"
    )


def test_synthetic_freeze_profile_and_train_only(tmp_path):
    import argparse
    import json

    import pytest

    from scripts.prepare_laya_training_data import freeze_dataset, load_frozen, prepare_synthetic
    from scripts.train_laya_pilot import training_inputs

    fixture = synthetic_fixture()
    report, rows = prepare_synthetic(fixture["cases"], fixture["reservation"])
    report = freeze_dataset(rows, report, fixture["questions"], tmp_path)
    manifest = tmp_path / "prepare.json"
    manifest.write_text(json.dumps(report), "utf-8")
    dataset = tmp_path / "frozen.jsonl"
    args = argparse.Namespace(
        dataset=dataset, manifest=manifest, data_profile="synthetic-experiment", split="train"
    )
    cases, _ = training_inputs(args)
    assert len(cases) == 90 and all(c["split"] == "train" for c in cases)
    with pytest.raises(ValueError, match="profile"):
        load_frozen(dataset, manifest, "train", "actual")
    args.split = "test"
    with pytest.raises(ValueError, match="train only"):
        training_inputs(args)
    args.split = "train"
    ledger = tmp_path / "test-consumed.json"
    ledger.write_text("{}", "utf-8")
    with pytest.raises(ValueError, match="consumed"):
        training_inputs(args)
    ledger.unlink()
    dataset.write_text(dataset.read_text("utf-8") + " ", "utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_frozen(dataset, manifest, "train", "synthetic-experiment")


def test_temperature_cannot_use_development_or_failed_rows():
    import pytest

    from scripts.evaluate_ollaya import choose_temperature

    row = {
        "split": "calibration",
        "status": "observed",
        "expected": "a",
        "probabilities": {"a": 0.5, "b": 0.5},
    }
    assert choose_temperature({"split": "calibration", "rows": [row]}) == 1
    with pytest.raises(ValueError, match="calibration"):
        choose_temperature({"split": "development", "rows": [row]})
    row["status"] = "error"
    with pytest.raises(ValueError, match="calibration"):
        choose_temperature({"split": "calibration", "rows": [row]})


def test_synthetic_candidate_id_label_leak_rejected_even_with_rebound_hash():
    from scripts.prepare_laya_training_data import digest, prepare_synthetic

    fixture = synthetic_fixture()
    row = next(r for r in fixture["cases"] if r["purpose"] == "capability_fit")
    row["state"]["candidate"]["id"] = "plausible-tool-fit"
    row["state_sha256"] = digest(row["state"])
    row["review"]["checked_state_sha256"] = row["state_sha256"]
    report, _ = prepare_synthetic(fixture["cases"], fixture["reservation"])
    assert any("Gold label leaks" in e for e in report["errors"])


def test_paired_comparison_keeps_failures_and_rejects_mismatch():
    import copy

    import pytest

    from scripts.evaluate_ollaya import paired_comparison

    rows = [
        {
            "repeat": 0,
            "id": p,
            "purpose": p,
            "split": "test",
            "state_sha256": "s",
            "question_sha256": "q",
            "group_id": "g",
            "expected": "a",
            "critical": True,
            "status": "error",
            "latency_ms": 1,
        }
        for p in ("relevance", "evidence_relation", "capability_fit")
    ]
    baseline = {
        "dataset_sha256": "d",
        "conditions": {"case_order": [r["id"] for r in rows]},
        "rows": rows,
        "memory_samples": [],
        "status": "incomplete",
    }
    result = paired_comparison(baseline, copy.deepcopy(baseline))
    assert all(
        r["baseline"]["count"] == 1
        and r["baseline"]["errors"] == 1
        and not r["improvement_gate_passed"]
        for r in result.values()
    )
    candidate = copy.deepcopy(baseline)
    candidate["rows"][0]["question_sha256"] = "different"
    with pytest.raises(ValueError, match="mismatch"):
        paired_comparison(baseline, candidate)


def test_retry_diagnostic_ids_and_settings(tmp_path):
    import argparse
    import json

    from scripts.train_laya_pilot import validate_run

    rows = [r for r in synthetic_fixture()["cases"] if r["split"] == "train"]
    ids = []
    for purpose, labels in data.LABELS.items():
        chosen = [
            next(r["id"] for r in rows if r["purpose"] == purpose and r["expected"] == label)
            for label in labels
        ]
        chosen += [r["id"] for r in rows if r["purpose"] == purpose and r["id"] not in chosen][
            : 5 - len(chosen)
        ]
        ids.extend(chosen)
    path = tmp_path / "ids.json"
    path.write_text(json.dumps(ids), "utf-8")
    args = argparse.Namespace(
        steps=300, learning_rate=1e-5, diagnostic_ids=path, data_profile="synthetic-experiment"
    )
    assert len(validate_run(args, rows)) == 15
    for rate in (1e-4, 6e-4):
        args.learning_rate = rate
        assert len(validate_run(args, rows)) == 15
    args.learning_rate = 1e-5
    for bad in [ids[:-1], ids[:-1] + [ids[0]], ids[:-1] + ["not-train"]]:
        path.write_text(json.dumps(bad), "utf-8")
        with pytest.raises(ValueError):
            validate_run(args, rows)
    args.diagnostic_ids = None
    for steps, rate in [(100, 3e-5), (301, 1e-5), (300, float("nan")), (300, 0)]:
        args.steps, args.learning_rate = steps, rate
        with pytest.raises(ValueError, match="approved"):
            validate_run(args, rows)


def test_retry_diagnostic_gate_needs_loss_and_decision_learning():
    from scripts.train_laya_pilot import diagnostic_passed

    before = {"step": 0, "mean_loss": 2.0, "correct": 5}
    after = {"step": 300, "mean_loss": 1.0, "correct": 6}
    assert diagnostic_passed([before, after], ["head.weight"])
    assert not diagnostic_passed([before, {**after, "correct": 5}], ["head.weight"])
    assert not diagnostic_passed([before, {**after, "mean_loss": 2.1}], ["head.weight"])
    assert not diagnostic_passed([before, after], ["encoder.weight"])
    assert not diagnostic_passed([before, {**after, "mean_loss": float("nan")}], ["head.weight"])


def test_retry_test_is_new_and_old_development_is_preserved():
    import json
    from pathlib import Path

    old = synthetic_fixture()
    new = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "evaluations/laya-finetuning/synthetic-retry-20260929.json"
        ).read_text("utf-8")
    )
    exposed = [r for r in old["cases"] if r["split"] == "test"]
    report, _ = data.prepare_synthetic(new["cases"], new["reservation"], exposed)
    assert report["status"] == "ready"
    reused = dict(new["reservation"])
    reused["families"] = {**reused["families"], "waterlab": "test"}
    rejected, _ = data.prepare_synthetic(new["cases"], reused)
    assert "New test reuses a previously exposed family" in rejected["errors"]
    old_states = {r["state_sha256"] for r in old["cases"]}
    assert all(r["state_sha256"] not in old_states for r in new["cases"] if r["split"] == "test")
    old_by_id = {r["id"]: r for r in old["cases"]}
    for row in new["cases"]:
        if row["split"] != "test":
            assert row["state"] == old_by_id[row["id"]]["state"]
            assert row["expected"] == old_by_id[row["id"]]["expected"]


def test_retry_selection_excludes_test_failed_and_diagnostic_candidates():
    import copy

    from scripts.evaluate_ollaya import select_development_candidate

    rows = []
    for purpose in data.LABELS:
        for i in range(15):
            rows.append(
                {
                    "id": f"{purpose}-{i}",
                    "purpose": purpose,
                    "expected": "yes",
                    "choice": "yes",
                    "state_sha256": "s",
                    "question_sha256": "q",
                    "critical": False,
                    "split": "development",
                    "status": "observed",
                    "repeat": 0,
                    "probabilities": {"yes": 0.8, "no": 0.2},
                }
            )
    baseline = {"split": "development", "status": "completed", "rows": rows, "dataset_sha256": "d"}
    training = {
        "status": "completed",
        "diagnostic": False,
        "max_steps": 100,
        "learning_rate": 1e-5,
        "dataset_sha256": "d",
    }
    candidates = {"A": {"report": copy.deepcopy(baseline), "training": training}}
    assert select_development_candidate(baseline, candidates)["selected"] == "A"
    for change in [
        lambda c: c["training"].update(diagnostic=True),
        lambda c: c["report"].update(split="test"),
        lambda c: c["report"]["rows"][0].update(status="error"),
        lambda c: c["report"]["rows"][0].update(state_sha256="changed"),
        lambda c: c["report"]["rows"][0].update(choice="no"),
    ]:
        damaged = copy.deepcopy(candidates)
        change(damaged["A"])
        assert select_development_candidate(baseline, damaged)["selected"] is None


def test_diagnostic_checkpoint_cannot_freeze(tmp_path, monkeypatch):
    import argparse
    import json

    from scripts import evaluate_ollaya as evaluate

    monkeypatch.setattr(data, "load_frozen", lambda *args: [])
    checkpoint = tmp_path / "checkpoint/model.safetensors"
    checkpoint.parent.mkdir()
    (tmp_path / "train.json").write_text(json.dumps({"diagnostic": True}), "utf-8")
    args = argparse.Namespace(
        checkpoint=checkpoint,
        dataset=tmp_path / "data",
        manifest=tmp_path / "manifest",
        data_profile="synthetic-experiment",
    )
    with pytest.raises(ValueError, match="Diagnostic"):
        evaluate.freeze_candidate(args)


def test_lr_probe_order_and_hash_guards(tmp_path):
    import copy
    import json
    from pathlib import Path

    from scripts.evaluate_ollaya import load_probes, probe_digest

    fixture = Path("evaluations/laya-finetuning/lr-probes-20260929.json")
    assert len(load_probes(fixture)) == 40
    original = json.loads(fixture.read_text(encoding="utf-8"))
    for kind in ("hash", "split", "label", "order"):
        payload = copy.deepcopy(original)
        row = payload["cases"][-1]
        if kind == "hash":
            row["state"]["text"] += "changed"
        elif kind == "split":
            row["split"] = "test"
        elif kind == "label":
            row["expected"] = "cancel_account"
        else:
            row["question"]["criteria"] = dict(reversed(list(row["question"]["criteria"].items())))
            row["question_sha256"] = probe_digest(row["question"])
        payload["sha256"] = probe_digest(payload["cases"])
        path = tmp_path / "probe.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            load_probes(path)


def test_high_learning_rates_only_bounded_synthetic():
    from argparse import Namespace

    from scripts.train_laya_pilot import validate_run

    for rate in (1e-4, 6e-4):
        args = Namespace(
            steps=300, learning_rate=rate, diagnostic_ids=None, data_profile="synthetic-experiment"
        )
        assert validate_run(args, []) == []
        for profile, steps in (
            ("actual", 300),
            ("public-pilot", 300),
            ("synthetic-experiment", 100),
        ):
            args.data_profile, args.steps = profile, steps
            with pytest.raises(ValueError):
                validate_run(args, [])


def test_probe_failures_are_retained_and_parity_rejects_mismatch():
    import copy
    from pathlib import Path

    from scripts.evaluate_ollaya import compare_probes, complete_probe_failures, load_probes

    cases = load_probes(Path("evaluations/laya-finetuning/lr-probes-20260929.json"))
    report = {"dataset_sha256": "same", "rows": []}
    complete_probe_failures(report, cases, "load failed")
    assert len(report["rows"]) == 40
    assert all(r["status"] == "not_run" for r in report["rows"])
    assert not compare_probes(report, report)["passed"]
    changed = copy.deepcopy(report)
    changed["rows"][0]["question_sha256"] = "changed"
    with pytest.raises(ValueError, match="mismatch"):
        compare_probes(report, changed)


def test_parity_ids_are_frozen_train_only():
    from scripts.train_laya_pilot import select_parity

    cases = [{"id": str(i)} for i in range(15)]
    ids = [c["id"] for c in cases]
    assert select_parity(cases, ids, "synthetic-experiment") == cases
    for bad in (ids[:-1], ids[:-1] + [ids[0]], ids[:-1] + ["test-case"], [{"id": "bad"}] * 15):
        with pytest.raises(ValueError):
            select_parity(cases, bad, "synthetic-experiment")
    with pytest.raises(ValueError):
        select_parity(cases, ids, "public-pilot")


def test_probe_cli_never_overwrites_an_existing_run(tmp_path, monkeypatch):
    import sys

    from scripts.evaluate_ollaya import main

    target = tmp_path / "probe.json"
    original = '{"status":"completed","rows":[]}'
    target.write_text(original)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_ollaya",
            "probe",
            "--data-profile",
            "synthetic-experiment",
            "--probe-dataset",
            "evaluations/laya-finetuning/lr-probes-20260929.json",
            "--output",
            str(tmp_path),
        ],
    )
    with pytest.raises(FileExistsError):
        main()
    assert target.read_text() == original


def test_all_final_test_backends_require_candidate_lock(monkeypatch):
    from argparse import Namespace
    from pathlib import Path

    from scripts.evaluate_ollaya import frozen_evaluation

    monkeypatch.setattr(data, "load_frozen", lambda *args: [])
    for backend, model in (
        ("ollaya", "laya:multilingual"),
        ("ollaya", "jev-laya:pilot"),
        ("semif", "product"),
    ):
        args = Namespace(
            dataset=Path("frozen.jsonl"),
            manifest=Path("prepare.json"),
            data_profile="synthetic-experiment",
            split="test",
            backend=backend,
            model=model,
            candidate_lock=None,
        )
        with pytest.raises(ValueError, match="Every final test"):
            frozen_evaluation(args)


def test_curve_fixture_balanced_nested_and_honest_review():
    fixture = json.loads(
        Path("evaluations/laya-finetuning/learning-curve-20260930.json").read_text("utf-8")
    )
    report, rows = data.prepare_synthetic(
        fixture["cases"], fixture["reservation"], profile="synthetic-learning-curve"
    )
    assert report["status"] == "ready", report["errors"]
    assert report["causal_families"] == report["groups"] == 360
    assert len(rows) == 5400
    assert len({data.digest(r["state"]) for r in rows}) == 5400
    assert all(
        not r["review"]["human_reviewed"] and not r["review"]["individual_manual_read"]
        for r in rows
    )
    reservation = copy.deepcopy(fixture["reservation"])
    reservation["train_subsets"]["450"][0] = next(r["id"] for r in rows if r["split"] == "test")
    _, errors = data.check_learning_curve(rows, reservation)
    assert any("nested train" in e or "Partial family" in e for e in errors)
    reservation = copy.deepcopy(fixture["reservation"])
    reservation["family_specs"]["api-00"].update(reservation["family_specs"]["source-00"])
    _, errors = data.check_learning_curve(rows, reservation)
    assert any("Repeated causal family" in e for e in errors)
    changed = copy.deepcopy(fixture["cases"])
    changed[0]["review"]["target_model_output_used_as_label"] = True
    report, _ = data.prepare_synthetic(
        changed, fixture["reservation"], profile="synthetic-learning-curve"
    )
    assert report["status"] == "not_ready"

    changed = copy.deepcopy(fixture["cases"])
    row = next(r for r in changed if r["purpose"] == "capability_fit")
    row["state"]["candidate"]["id"] += "-0"
    row["state_sha256"] = row["review"]["checked_state_sha256"] = data.digest(row["state"])
    report, _ = data.prepare_synthetic(
        changed, fixture["reservation"], profile="synthetic-learning-curve"
    )
    assert any("ID must be identical" in e for e in report["errors"])


def test_curve_cache_cannot_mix_input_split_or_encoder(tmp_path):
    from scripts.train_laya_pilot import check_feature_cache, sha

    case = dict(id="row", state="input", question="question")
    target = tmp_path / "row.pt"
    target.write_bytes(b"local features")
    meta = dict(
        status="completed",
        base_sha256="base",
        tokenizer_files={},
        dataset_sha256="data",
        split="train",
        dtype="float32",
        rows=[
            dict(
                id="row",
                file="row.pt",
                sha256=sha(target),
                state_sha256=data.digest("input"),
                question_sha256=data.digest("question"),
            )
        ],
    )
    (tmp_path / "cache.json").write_text(json.dumps(meta))
    assert check_feature_cache(tmp_path, [case], "base", {}, "data", "train")
    for model, split, state in (
        ("other", "train", "input"),
        ("base", "test", "input"),
        ("base", "train", "changed"),
    ):
        with pytest.raises(ValueError):
            check_feature_cache(tmp_path, [{**case, "state": state}], model, {}, "data", split)
    target.write_bytes(b"changed features")
    with pytest.raises(ValueError, match="feature file changed"):
        check_feature_cache(tmp_path, [case], "base", {}, "data", "train")


def test_curve_training_settings_cannot_reduce_exposure_or_leak_split(tmp_path):
    from scripts.train_laya_pilot import validate_run

    ids = [str(i) for i in range(450)]
    selection = tmp_path / "ids.json"
    selection.write_text(json.dumps(ids))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"train_subsets": {"450": ids}}))
    args = SimpleNamespace(
        data_profile="synthetic-learning-curve",
        stage="train",
        split="train",
        max_run_seconds=5400,
        train_ids=selection,
        diagnostic_ids=None,
        manifest=manifest,
        epochs=6,
        learning_rate=6e-4,
        seed=20260928,
        effective_batch=15,
        feature_cache=tmp_path,
    )
    cases = [{"id": i} for i in ids]
    assert len(validate_run(args, cases)) == 450
    for field, value in (
        ("epochs", 7),
        ("seed", 0),
        ("effective_batch", 1),
        ("split", "test"),
        ("max_run_seconds", 5401),
    ):
        bad = copy.copy(args)
        setattr(bad, field, value)
        with pytest.raises(ValueError):
            validate_run(bad, cases)
    selection.write_text(json.dumps(ids[:-1] + ["test-row"]))
    with pytest.raises(ValueError, match="train subset"):
        validate_run(args, cases)


def test_curve_resamples_groups_not_seed_rows_and_blocks_missing_runs(tmp_path, monkeypatch):
    from scripts.evaluate_ollaya import sha, summarize_learning_curve

    rows = [
        dict(
            id=f"r{i}",
            state_sha256=f"s{i}",
            question_sha256=data.digest({"criteria": {"yes": "yes", "no": "no"}}),
            purpose=p,
            expected="yes",
            critical=True,
            group_id=f"g{i // 5}",
            split="development",
            status="observed",
            repeat=0,
            choice="no",
            probabilities={"yes": 0.1, "no": 0.9},
            latency_ms=1,
        )
        for p in data.LABELS
        for i in range(150)
    ]
    # IDs identify each purpose too; groups are shared causal scenarios with five cases/purpose.
    for row in rows:
        row["id"] += row["purpose"]
    (tmp_path / "frozen").mkdir()
    dataset = tmp_path / "frozen/frozen.jsonl"
    dataset.write_text("fixed dataset")
    frozen_hash = sha(dataset)
    cases = [{**row, "question": {"criteria": {"yes": "yes", "no": "no"}}} for row in rows]
    monkeypatch.setattr(data, "load_frozen", lambda *args: cases)
    baseline = dict(status="completed", split="development", dataset_sha256=frozen_hash, rows=rows)
    base_path = tmp_path / "features/development"
    base_path.mkdir(parents=True)
    (base_path / "baseline-development.json").write_text(json.dumps(baseline))
    for size, epochs in ((450, 6), (1350, 6), (4050, 6), (450, 54), (1350, 18)):
        for seed in (20260928, 20260929, 20260930):
            path = tmp_path / f"n{size}-e{epochs}-s{seed}"
            path.mkdir()
            training = dict(
                status="completed",
                seed=seed,
                training_ids=[str(i) for i in range(size)],
                data_profile="synthetic-learning-curve",
                batch_size=15,
                learning_rate=6e-4,
                dataset_sha256=frozen_hash,
                epochs=epochs,
                max_steps=size // 15 * epochs,
                exposures={str(i): epochs for i in range(size)},
                elapsed_seconds=1,
            )
            (path / "train.json").write_text(json.dumps(training))
            report = copy.deepcopy(baseline)
            for row in report["rows"]:
                row.update(choice="yes", probabilities={"yes": 0.9, "no": 0.1})
            (path / "development.json").write_text(json.dumps(report))
    result = summarize_learning_curve(tmp_path)
    assert result["status"] == "complete"
    assert result["selected_setting"] == "n450-e6"
    assert result["selected_seed"] == 20260929
    interval = result["settings"]["n450-e6"]["versus_original"]["relevance"]
    assert interval["groups"] == 30 and interval["distinct_cases"] == 150
    assert interval["bootstrap_95"] == [1, 1]
    (tmp_path / "n4050-e6-s20260930/train.json").unlink()
    (tmp_path / "execution.json").write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "name": "n4050-e6-s20260930",
                        "status": "incomplete",
                        "exit_code": 124,
                        "reason": "time_limit",
                    }
                ]
            }
        )
    )
    result = summarize_learning_curve(tmp_path)
    assert result["status"] == "incomplete" and result["conclusion"] == "필요량 미확정"
    assert result["largest_vs_original"] == "최대 규모 미완료로 비교 미실행"
    assert not result["saturation"]
    missing = result["missing_runs"][0]
    assert missing["execution"]["reason"] == "time_limit"
    assert missing["planned_optimizer_steps"] == 1620
    assert len(missing["evaluation_rows"]) == 450


def test_evaluation_checks_frozen_gold_even_if_all_model_reports_agree():
    from scripts.evaluate_ollaya import validate_frozen_evaluation_rows

    case = dict(
        id="fixed",
        state_sha256="state",
        purpose="relevance",
        expected="yes",
        critical=True,
        group_id="workflow",
        question={"criteria": {"yes": "yes", "no": "no"}},
    )
    row = {k: v for k, v in case.items() if k != "question"}
    row.update(
        question_sha256=data.digest(case["question"]),
        split="test",
        repeat=0,
        status="observed",
        choice="yes",
        probabilities={"yes": 0.9, "no": 0.1},
    )
    validate_frozen_evaluation_rows({"rows": [row]}, [case], "test", 1)
    for field, value in (
        ("expected", "no"),
        ("question_sha256", "changed"),
        ("state_sha256", "changed"),
        ("group_id", "other"),
        ("repeat", 1),
    ):
        with pytest.raises(ValueError, match="frozen"):
            validate_frozen_evaluation_rows({"rows": [{**row, field: value}]}, [case], "test", 1)
    failed = {**row, "status": "not_run", "reason": "memory_limit"}
    validate_frozen_evaluation_rows({"rows": [failed]}, [case], "test", 1)
    with pytest.raises(ValueError, match="frozen"):
        validate_frozen_evaluation_rows({"rows": [row, row]}, [case], "test", 1)


def test_curve_rejects_reordered_question_even_if_canonical_content_hash_matches(tmp_path):
    source = Path(".local/laya-finetuning/curve-20260930/frozen/frozen.jsonl")
    # Public fixture keeps this check runnable without private/local artifacts.
    fixture = json.loads(
        Path("evaluations/laya-finetuning/learning-curve-20260930.json").read_text("utf-8")
    )
    report, rows = data.prepare_synthetic(
        fixture["cases"], fixture["reservation"], profile="synthetic-learning-curve"
    )
    report = data.freeze_dataset(rows, report, fixture["questions"], tmp_path)
    source = tmp_path / "frozen.jsonl"
    rows = data.read_jsonl(source)
    for row in rows:
        row["question"]["criteria"] = dict(reversed(list(row["question"]["criteria"].items())))
    source.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    import hashlib

    report["file_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="presentation order"):
        data.load_frozen(source, target, "train", "synthetic-learning-curve")


def test_curve_calibration_cannot_use_partial_success():
    from scripts.evaluate_ollaya import choose_temperature

    with pytest.raises(ValueError, match="450 calibration"):
        choose_temperature(
            dict(
                data_profile="synthetic-learning-curve",
                status="completed",
                split="calibration",
                rows=[],
            )
        )


def test_reload_parity_supports_fifteen_and_rejects_mismatched_input():
    from scripts.verify_laya_pilot import check_reload, parity_count

    predictions = [
        dict(
            id=str(i),
            state=f"input-{i}",
            purpose="relevance",
            question={},
            answer=dict(choice="yes", probabilities={"yes": 0.8, "no": 0.2}),
        )
        for i in range(15)
    ]
    training = dict(
        status="completed", checkpoint_sha256="same", after_fp32=copy.deepcopy(predictions)
    )
    reference = dict(
        status="completed",
        checkpoint_sha256="same",
        parity_ids_sha256="ids",
        predictions=predictions,
    )
    assert check_reload(training, reference) == 15
    bad = copy.deepcopy(reference)
    bad["predictions"][0]["state"] = "different input"
    with pytest.raises(ValueError, match="input/order"):
        check_reload(training, bad)
    bad = copy.deepcopy(reference)
    bad["predictions"][0]["answer"]["probabilities"] = {"yes": 0.7, "no": 0.3}
    with pytest.raises(ValueError, match="probability"):
        check_reload(training, bad)
    bad = copy.deepcopy(reference)
    bad["predictions"][1]["id"] = "0"
    with pytest.raises(ValueError, match="IDs/count"):
        parity_count(bad)


def test_direct_runner_maps_supplied_label_order_and_rejects_invalid_output():
    from scripts.evaluate_ollaya import runner_choice

    q = dict(type="choice", criteria={"unfit": "no", "fit": "yes"})
    raw = dict(questions=[dict(logits=[-10, 10], act_logits=[20, -20])], state_truncated=False)
    answer = runner_choice(raw, q)
    assert answer["choice"] == "fit"
    assert list(answer["raw_distribution"]) == ["unfit", "fit"]
    reversed_q = {**q, "criteria": dict(reversed(list(q["criteria"].items())))}
    assert runner_choice(raw, reversed_q)["choice"] == "unfit"
    for invalid in [
        {**raw, "state_truncated": True},
        {**raw, "questions": [dict(logits=[float("nan"), 0])]},
        {**raw, "questions": [dict(logits=[1])]},
        {**raw, "questions": [dict(logits=[1, 2], act_logits=[float("nan"), 1])]},
        {**raw, "questions": [dict(logits=[1, 2], act_logits=[0, 1, 2])]},
        {**raw, "questions": []},
    ]:
        with pytest.raises(ValueError):
            runner_choice(invalid, q)


def test_direct_runner_explicit_threads_integrity_and_cleanup(tmp_path, monkeypatch):
    import io
    import json

    from scripts import evaluate_ollaya as evaluate
    from scripts import train_laya_pilot as trainer

    store = tmp_path / "store"
    blobs = store / "blobs"
    blobs.mkdir(parents=True)
    layers = []
    payloads = {
        "config.v1+json": {},
        "decision": dict(family="laya"),
        "calibration": dict(temperature=[1, 1, 1], temperature_by_options={}),
        "tokenizer": {"fixture": "tokenizer"},
        "graph.onnx": {"fixture": "graph"},
    }
    for kind, payload in payloads.items():
        file = blobs / kind
        file.write_text(json.dumps(payload))
        digest = evaluate.sha(file)
        file.rename(blobs / ("sha256-" + digest))
        layers.append(dict(mediaType="application/vnd.ollaya." + kind, digest="sha256:" + digest))
    manifest = store / "manifests/ollaya.dev/library/laya/multilingual"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(dict(config=layers[0], layers=layers[1:])))
    binary = tmp_path / ".local/ollaya-evaluation/runtime/bin/ollaya.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"pinned-installed-binary")
    monkeypatch.setattr(evaluate, "ROOT", tmp_path)
    monkeypatch.setattr(trainer, "memory_gib", lambda: 8)
    children = []

    class Child:
        def __init__(self, command, **kwargs):
            self.command = command
            self.pid = 123
            self.code = None
            self.stdout = io.StringIO(
                json.dumps(dict(port=12345, device="cpu", precision="fp32", engine="onnx"))
            )
            children.append(self)

        def poll(self):
            return self.code

        def terminate(self):
            self.code = 0

        def wait(self, timeout):
            return self.code

    class Client:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def call(self, path):
            assert path == "/health"
            return dict(device="cpu", precision="fp32", engine="onnx")

    monkeypatch.setattr(evaluate.subprocess, "Popen", Child)
    monkeypatch.setattr(evaluate, "Client", Client)
    output = tmp_path / "valid"
    output.mkdir()
    with evaluate.laya_runner(store, "laya:multilingual", output) as (client, pid, conditions):
        assert pid == 123 and client.endpoint == "http://127.0.0.1:12345"
        assert children[-1].command[-4:] == ["--device", "cpu", "--threads", "4"]
        assert conditions["intra_op_threads"] == 4
    assert children[-1].poll() == 0 and children[-1].stdout.closed
    original_manifest = manifest.read_text()
    mapped_calibration = blobs / "input-conditioned-calibration"
    mapped_calibration.write_text(
        json.dumps(
            {
                **payloads["calibration"],
                "temperature_map": {"kind": "von-entropy-length-v1", "bias": 2},
            }
        )
    )
    mapped_hash = evaluate.sha(mapped_calibration)
    mapped_calibration.rename(blobs / ("sha256-" + mapped_hash))
    mapped = json.loads(original_manifest)
    mapped["layers"][1]["digest"] = "sha256:" + mapped_hash
    manifest.write_text(json.dumps(mapped))
    with pytest.raises(ValueError, match="unit model calibration"):
        with evaluate.laya_runner(store, "laya:multilingual", output):
            pytest.fail("Input-conditioned calibration must not be silently bypassed")
    manifest.write_text(original_manifest)
    target = blobs / layers[-1]["digest"].replace(":", "-")
    target.write_text("changed graph")
    with pytest.raises(ValueError, match="integrity"):
        with evaluate.laya_runner(store, "laya:multilingual", output):
            pytest.fail("Changed model must not load")
    assert len(children) == 1


def test_curve_runtime_comparison_rejects_unverified_threads_and_changed_binary():
    from scripts.evaluate_ollaya import paired_comparison

    report = dict(
        data_profile="synthetic-learning-curve",
        backend="ollaya",
        conditions=dict(intra_op_threads=4, execution_mode="existing_ollaya_runner"),
        runner=dict(hello=dict(device="cpu", precision="fp32"), binary_sha256="pinned"),
    )
    bad = copy.deepcopy(report)
    bad["conditions"]["intra_op_threads"] = None
    with pytest.raises(ValueError, match="four-thread"):
        paired_comparison(report, bad)
    bad = copy.deepcopy(report)
    bad["runner"]["binary_sha256"] = "different"
    with pytest.raises(ValueError, match="binary changed"):
        paired_comparison(report, bad)
