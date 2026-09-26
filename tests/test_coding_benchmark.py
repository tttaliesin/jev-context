import json
import tempfile
from pathlib import Path

import pytest

from scripts.coding_benchmark import ROOT, TASKS, prepare_suite, verify_trial
from scripts.coding_contexts import seal_contexts


def record_scratch(monkeypatch):
    """Paths the grader creates under .t/, whether via mkdtemp or TemporaryDirectory."""
    created, mkdtemp = [], tempfile.mkdtemp

    def recording(*args, **kwargs):
        path = Path(mkdtemp(*args, **kwargs))
        if path.parent == ROOT / ".t":
            created.append(path)
        return str(path)

    monkeypatch.setattr(tempfile, "mkdtemp", recording)
    return created


def test_seeded_trials_require_passing_reference_and_failing_start(tmp_path, monkeypatch):
    scratch = record_scratch(monkeypatch)
    # A suite inside another repository must not inherit its import-path settings.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["' + (ROOT / "src").as_posix() + '"]\n'
    )
    # Parent pytest options must not redirect or break the independent grading process.
    monkeypatch.setenv("PYTEST_ADDOPTS", "--basetemp=missing-parent/nested-temp")
    suite = prepare_suite(tmp_path / "suite")
    assert len(suite["trials"]) == 6
    assert all(task["reference"]["passed"] for task in suite["tasks"])
    assert all(task["seed"]["exit_code"] == 1 for task in suite["tasks"])
    assert all(task["seed"]["failures"] > 0 for task in suite["tasks"])
    # Every grading run removes its in-project pytest scratch directory.
    assert len(scratch) == 6
    assert all(path.parent == ROOT / ".t" and not path.exists() for path in scratch)

    evidence = [{"id": "policy", "text": "한국어 원문 보존"}]
    with pytest.raises(ValueError, match="observed judgment"):
        seal_contexts(tmp_path / "suite", evidence, {}, {})
    assert not list((tmp_path / "suite/trials").glob("*/CONTEXT.json"))
    observations = {
        task: [{"id": "policy", "result": {"status": "observed", "answers": []}}] for task in TASKS
    }
    seal_contexts(tmp_path / "suite", evidence, observations, {"fixture_only": True})
    off = json.loads((tmp_path / "suite/trials/json-off/CONTEXT.json").read_text("utf-8"))
    observe = json.loads((tmp_path / "suite/trials/json-observe/CONTEXT.json").read_text("utf-8"))
    assert off["evidence"] == observe["evidence"] == evidence
    assert not off["judgments"] and len(observe["judgments"]) == 1
    with pytest.raises(ValueError, match="already frozen"):
        seal_contexts(tmp_path / "suite", evidence, observations, {})

    trial = next(t for t in suite["trials"] if t["id"] == "json-off")
    target = Path(trial["directory"]) / trial["allowed_file"]
    target.write_bytes((tmp_path / "suite/references/json" / trial["allowed_file"]).read_bytes())
    verified = verify_trial(tmp_path / "suite", "json-off")
    assert verified["valid"] and verified["passed"]
    assert verified["check"]["tests"] == 11

    check = tmp_path / "suite/checks/check_json.py"
    check.write_text("def test_fake(): assert True\n")
    rejected = verify_trial(tmp_path / "suite", "json-off")
    assert rejected["valid"] is False
    assert "grader_changed" in rejected["scope_violations"]

    manifest = tmp_path / "suite/suite.json"
    manifest.write_text("{}")
    rejected = verify_trial(tmp_path / "suite", "json-off")
    assert rejected["valid"] is False
    assert "suite_manifest_changed" in rejected["scope_violations"]


def test_existing_suite_is_never_overwritten(tmp_path):
    destination = tmp_path / "suite"
    destination.mkdir()
    marker = destination / "suite.json"
    marker.write_text(json.dumps({"user_owned": True}))
    with pytest.raises(FileExistsError):
        prepare_suite(destination)
    assert json.loads(marker.read_text()) == {"user_owned": True}
