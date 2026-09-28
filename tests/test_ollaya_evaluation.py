import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "evaluate_ollaya", Path(__file__).resolve().parents[1] / "scripts/evaluate_ollaya.py"
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def row(**changes):
    return {
        "id": "sample",
        "split": "development",
        "purpose": "relevance",
        "expected": "relevant",
        "critical": False,
        "choice": "relevant",
        "probabilities": {"relevant": 0.8, "irrelevant": 0.1, "insufficient_evidence": 0.1},
        "status": "observed",
        "latency_ms": 20,
        **changes,
    }


def test_frozen_split_and_labels():
    cases, questions, _ = evaluation.fixtures()
    assert len(cases) == len({c["id"] for c in cases}) == 60
    for split in ("development", "validation"):
        for purpose in questions["baseline"]:
            rows = [c for c in cases if c["split"] == split and c["purpose"] == purpose]
            assert len(rows) == 10
            assert all(c["expected"] in questions["baseline"][purpose]["criteria"] for c in rows)
    assert len({str(c["state"]) for c in cases}) == 60


def test_temperature_preserves_argmax_and_composes():
    probabilities = row()["probabilities"]
    first = evaluation.scale(probabilities, 2)
    second = evaluation.scale(first, 3)
    assert second == pytest.approx(evaluation.scale(probabilities, 6))
    assert max(second, key=second.get) == "relevant"
    assert sum(second.values()) == pytest.approx(1)
    with pytest.raises(ValueError):
        evaluation.scale(probabilities, 0)


def test_selection_rejects_validation_and_failed_observations():
    for bad in (row(split="validation"), row(status="error")):
        with pytest.raises(ValueError):
            evaluation.select({"baseline": [bad]})


def test_accept_none_and_insufficient_evidence_is_never_accepted():
    wrong = row(choice="irrelevant", critical=True)
    selected = evaluation.select({"baseline": [wrong] * 30})
    assert selected["threshold"] == 1.01
    assert evaluation.metrics([row(choice="insufficient_evidence")])["accepted"] == 0


def test_failures_stay_in_denominator_and_abort_is_visible(tmp_path):
    calls = []

    def fail(case):
        calls.append(case)
        raise TimeoutError("deadline")

    cases = [row(id=str(i), language="ko") for i in range(5)]
    rows = evaluation.run_rows(cases, fail, tmp_path / "rows.json")
    report = evaluation.metrics(rows)
    assert len(calls) == report["errors"] == 3
    assert report["not_run"] == 2
    assert report["count"] == 5
    assert report["correct"] == 0
    assert report["nll_observed"] is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1",
        "http://192.0.2.1",
        "http://localhost",
        "http://127.0.0.1/path",
        "http://user@127.0.0.1",
    ],
)
def test_endpoint_rejects_non_loopback_contract(endpoint):
    with pytest.raises(ValueError):
        evaluation.Client(endpoint)
