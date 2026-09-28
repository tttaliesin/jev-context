import json
import sqlite3
import struct
from types import SimpleNamespace

import pytest

from jev_context.laya_worker import validate_rotary_config
from scripts import prepare_laya_training_data as data
from scripts.train_laya_pilot import compatible_encoder_config, safetensors_header


def candidate(name, work, source, text):
    return {
        "id": name,
        "work_id": work,
        "source_refs": [{"source_id": source, "locator": source}],
        "state": {"query": "query", "candidate": text},
        "purpose": "relevance",
        "expected": "relevant",
        "critical": False,
        "review": {"status": "human_reviewed", "reviewer": "tester", "reviewed_at": "2026-09-28"},
    }


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


def test_identical_source_text_cannot_cross_groups_even_when_ids_differ():
    rows = [candidate("a", "work1", "doc1", "same"), candidate("b", "work2", "doc2", "same")]
    assert len(set(data.assign_groups(rows))) == 1


def test_model_prediction_is_not_a_human_gold_label():
    row = candidate("a", "work1", "doc1", "first")
    row.update(expected=None, model_prediction="relevant", review={"status": "pending"})
    report, prepared = data.prepare_reviewed([row], minimum=1)
    assert report["status"] == "not_ready"
    assert any("Human review" in e for e in report["errors"])
    assert any("Gold label" in e for e in report["errors"])
    assert not prepared


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
