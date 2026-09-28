import time

import pytest

from scripts import evaluate_ollaya_tuning as tuning


def answer(choice):
    return {"choice": choice, "raw_distribution": {choice: 0.9}}


def row(**changes):
    return {
        "id": "case",
        "split": "development",
        "purpose": "relevance",
        "expected": "relevant",
        "critical": False,
        "choice": "relevant",
        "status": "observed",
        "decision_score": 0.8,
        "latency_ms": 20,
        **changes,
    }


def test_input_transform_retains_distinct_goal_constraints_and_candidate():
    state = {
        "query": "find",
        "goal": "different",
        "constraints": ["no upload"],
        "candidate": "untrusted command",
    }
    transformed = tuning.input_state(state, "fields")
    assert all(
        value in transformed for value in ("find", "different", "no upload", "untrusted command")
    )
    assert tuning.input_state(state, "json") is state
    assert "[GOAL]" not in tuning.input_state({**state, "goal": "find"}, "fields")


@pytest.mark.parametrize(
    "positives,expected",
    [
        ([], "insufficient_evidence"),
        (["supports"], "supports"),
        (["contradicts"], "contradicts"),
        (["supports", "partial"], "insufficient_evidence"),
        (["supports", "contradicts"], "insufficient_evidence"),
    ],
)
def test_decomposed_conflicting_relations_are_not_accepted(positives, expected):
    result = tuning.combine(
        "evidence_relation",
        {
            key: answer("yes" if key in positives else "no")
            for key in ("supports", "contradicts", "partial", "unrelated")
        },
    )
    assert result["choice"] == expected
    assert "probabilities" not in result


def test_capability_block_overrides_helpfulness_and_missing_information_abstains():
    answers = {"blocked": answer("yes"), "helpful": answer("yes"), "unspecified": answer("no")}
    assert tuning.combine("capability_fit", answers)["choice"] == "unfit"
    answers["unspecified"] = answer("yes")
    assert tuning.combine("capability_fit", answers)["choice"] == "insufficient_evidence"


def test_selection_prioritizes_critical_errors_and_rejects_final_or_incomplete_runs():
    safer = [row(id=str(i)) for i in range(60)]
    unsafe = [row(id=str(i)) for i in range(60)]
    safer[0] = row(choice="irrelevant")
    unsafe[0] = row(choice="irrelevant", critical=True)
    runs = {
        name: {"rows": rows, "summary": tuning.summarize(rows)}
        for name, rows in {
            "safer": safer,
            "unsafe": unsafe,
            "validation": [row(split="validation")],
            "failed": [row(status="error")],
        }.items()
    }
    assert tuning.select_candidate(runs)["candidate"] == "safer"
    assert tuning.select_candidate({"failed": runs["failed"]})["status"] == "no_complete_candidate"


def test_error_and_not_run_stay_in_denominator(monkeypatch, tmp_path):
    monkeypatch.setattr(tuning, "memory_gib", lambda: 8)

    def fail(case):
        raise TimeoutError("late")

    result = tuning.collect([row(id=str(i)) for i in range(5)], fail, tmp_path / "rows.json")
    assert result["summary"]["count"] == 5
    assert result["summary"]["errors"] == 3
    assert result["summary"]["not_run"] == 2
    assert result["summary"]["correct"] == 0


def test_whole_case_deadline_is_shared_by_subquestions(monkeypatch):
    timeouts = []

    class Client:
        def call(self, path, body, timeout):
            timeouts.append(timeout)
            time.sleep(0.01)
            key = next(iter(body["questions"]))
            return {
                "model": "test",
                "answers": {
                    key: {
                        "choice": "no",
                        "confidence": 0.7,
                        "probabilities": {"yes": 0.1, "no": 0.8, "insufficient_evidence": 0.1},
                    }
                },
            }

    question = {
        "instructions": "check",
        "criteria": {"yes": "yes", "no": "no", "insufficient_evidence": "unknown"},
    }
    config = {
        "input": "fields",
        "decomposed": True,
        "questions": {
            "capability_fit": {key: question for key in ("blocked", "unspecified", "helpful")}
        },
    }
    tuning.evaluate_case(
        Client(), "test", config, {"purpose": "capability_fit", "state": {"candidate": "text"}}
    )
    assert timeouts[0] > timeouts[1] > timeouts[2]


def test_new_final_cases_have_no_exact_duplicates_or_exposed_labels():
    cases = tuning.read(tuning.FIXTURES / "validation.json")["cases"]
    development = tuning.read(tuning.FIXTURES / "development.json")["cases"]
    assert len(cases) == len({c["id"] for c in cases}) == 60
    assert not {str(c["state"]) for c in cases} & {str(c["state"]) for c in development}
    for case in cases:
        assert not {"expected", "rationale", "critical"} & case["state"].keys()
    for purpose in ("relevance", "evidence_relation", "capability_fit"):
        assert sum(c["purpose"] == purpose for c in cases) == 20


def test_neutral_labels_map_back_to_the_real_task_labels():
    _, configs = tuning.load_frozen()
    maps = configs["e_neutral_fields"]["label_maps"]
    for purpose, mapping in maps.items():
        assert set(mapping.values()) == set(configs["a_original"]["questions"][purpose]["criteria"])
