import json

import pytest

from scripts.evaluate_judgment_revision import ROOT, load_comparison, summarize


def test_comparison_rejects_duplicate_cases_instead_of_counting_as_independent(tmp_path):
    original = ROOT / "models/judgment-validation.json"
    duplicate = tmp_path / "same-cases.json"
    duplicate.write_bytes(original.read_bytes())
    with pytest.raises(ValueError, match="Duplicate case ID"):
        load_comparison(ROOT / "models/question-templates-v1.json", None, [original, duplicate])


def test_candidate_cannot_relabel_wrong_answer_as_correct(tmp_path):
    path = tmp_path / "candidate.json"
    template = json.loads((ROOT / "models/question-templates-v1.json").read_text())
    template["questions"]["capability_fit"]["options"]["pretend_fit"] = "success"
    path.write_text(json.dumps(template))
    with pytest.raises(ValueError, match="label contracts"):
        load_comparison(ROOT / "models/question-templates-v1.json", path, [])


def test_repeats_are_not_reported_as_independent_cases():
    rows = [
        {
            "id": "one",
            "cohort": "fresh",
            "variant": "candidate",
            "language": "ko",
            "correct": choice == "unfit",
            "status": "observed",
            "choice": choice,
            "expected": "unfit",
        }
        for choice in ("unfit", "fit", "unfit")
    ]
    result = summarize(rows)[0]
    assert result["cases"] == 1
    assert result["attempts"] == 3
    assert result["always_correct_cases"] == 0
    assert result["unstable_cases"] == 1
    assert result["false_fit"] == 1
