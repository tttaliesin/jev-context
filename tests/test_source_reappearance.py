import hashlib
import json
from dataclasses import replace

import pytest

from jev_context.common import uid
from jev_context.policy import Config
from jev_context.service import Service


def call(service, tool, **args):
    args.update(contract_version="2.0", request_id=uid("request"))
    if tool in {"source_sync", "data_forget"} or "create" in args:
        args.setdefault("mutation_id", uid("mutation"))
    return service.call(tool, args)


@pytest.fixture
def resumed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    config = Config(
        root, tmp_path / "state", "project-reappearance", ("*.md",), contract_version="2.0"
    )
    service = Service(config)
    work = call(
        service,
        "work_open",
        create={
            "title": "Resume requirements",
            "goal": "Preserve the current exception",
            "scope": {"mode": "investigate", "constraints": ["Do not start a model"]},
            "origin": {"quote": "Resume the saved work"},
        },
    )
    assert work["outcome"] == "ok"
    case = {"service": service, "config": config, "work_id": work["data"]["work_id"]}
    try:
        yield case
    finally:
        case["service"].close()


def prepare(case, **args):
    return call(
        case["service"],
        "context_prepare",
        work_id=case["work_id"],
        query="resume",
        judge_mode="off",
        budget_bytes=16000,
        **args,
    )


def register_missing(case, *, never_present=False, name="rules.md"):
    path = case["config"].project_root / name
    if not never_present:
        path.write_bytes(b"resume: original exception\n")
    registered = call(
        case["service"], "source_sync", items=[{"kind": "file", "relative_path": name}]
    )
    assert registered["outcome"] == "ok"
    source = registered["data"]["items"][0]
    if not never_present:
        path.unlink()
        assert prepare(case, source_ids=[source["source_id"]])["outcome"] == "partial"
    assert case["service"].sources.get(source["source_id"])["status"] == "missing"
    return path, source


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("never_present", [False, True])
def test_missing_status_and_reappearance_survive_restart(resumed, explicit, never_present):
    path, source = register_missing(resumed, never_present=never_present)
    resumed["service"].close()
    resumed["service"] = Service(resumed["config"])
    service = resumed["service"]
    scope = {"source_ids": [source["source_id"]]} if explicit else {}
    state = call(service, "workspace_status")["data"]["stale_sources"]
    assert state["checked"] == state["count"] == 1
    assert state["items"][0]["reason"] == "not_found"
    assert prepare(resumed, **scope)["outcome"] == "partial"

    path.write_bytes(b"resume: restored exception\n")
    (path.parent / "unregistered.md").write_bytes(b"resume: not registered\n")
    assert call(service, "workspace_status")["data"]["stale_sources"]["count"] == 1
    packet = prepare(resumed, **scope)
    assert packet["outcome"] == "ok"
    evidence = packet["data"]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["text"] == "resume: restored exception\n"
    assert evidence[0]["source_id"] == source["source_id"]
    assert evidence[0]["revision"] != source.get("revision")
    assert evidence[0]["freshness"] == "verified_at_read"
    state = call(service, "workspace_status")["data"]
    assert state["sources"] == 1 and state["stale_sources"]["count"] == 0
    assert service.engine is None


@pytest.mark.parametrize("restored", ["absent", "same", "changed", "first"])
def test_read_only_reappearance_is_partial_without_database_changes(resumed, restored):
    path, source = register_missing(resumed, never_present=restored == "first")
    if restored != "absent":
        path.write_bytes(
            b"resume: original exception\n" if restored == "same" else b"resume: new exception\n"
        )
    resumed["service"].close()
    database = resumed["config"].db_path
    before_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    resumed["service"] = Service(replace(resumed["config"], read_only=True))
    service = resumed["service"]
    before_meta = service.store.meta()
    args = {"source_ids": [source["source_id"]]}
    if restored == "same":
        args["required_refs"] = [{k: source[k] for k in ("source_id", "revision")}]
    packet = prepare(resumed, **args)
    assert packet["outcome"] == "partial"
    observations = packet["data"]["coverage"]["source_observations"]
    assert observations[source["source_id"]] == (
        "missing" if restored == "absent" else "changed_not_synced"
    )
    assert not any(e["freshness"] == "verified_at_read" for e in packet["data"]["evidence"])
    if restored == "same":
        assert packet["data"]["evidence"][0]["role"] == "required_evidence"
        assert packet["data"]["evidence"][0]["text"] == "resume: original exception\n"
    assert service.sources.get(source["source_id"])["status"] == "missing"
    assert service.store.meta() == before_meta
    service.close()
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before_hash
    resumed["service"] = Service(replace(resumed["config"], read_only=True))


def test_empty_search_scope_stays_empty_but_preserves_required_revision(resumed):
    path, source = register_missing(resumed)
    path.write_bytes(b"resume: newer exception\n")
    empty = prepare(resumed, source_ids=[])
    assert empty["outcome"] == "ok"
    assert empty["data"]["evidence"] == []
    assert empty["data"]["coverage"]["source_observations"] == {}
    assert resumed["service"].sources.get(source["source_id"])["status"] == "missing"
    required = prepare(
        resumed,
        source_ids=[],
        required_refs=[{k: source[k] for k in ("source_id", "revision")}],
    )
    assert required["outcome"] == "ok"
    assert len(required["data"]["evidence"]) == 1
    old = required["data"]["evidence"][0]
    assert old["freshness"] == "historical" and old["revision"] == source["revision"]
    assert old["role"] == "required_evidence" and "original exception" in old["text"]
    assert resumed["service"].sources.get(source["source_id"])["status"] == "available"


def test_reappearance_rechecks_policy_before_reading(resumed, monkeypatch):
    path, source = register_missing(resumed)
    path.write_bytes(b"resume: forbidden after policy change\n")
    resumed["service"].close()
    service = Service(replace(resumed["config"], exclude_paths=("rules.md",), policy_revision=2))
    resumed["service"] = service

    def must_not_read(_):
        raise AssertionError("Policy-denied file reached source preparation")

    monkeypatch.setattr(service.sources, "prepare", must_not_read)
    explicit = prepare(resumed, source_ids=[source["source_id"]])
    assert explicit["outcome"] == "error" and explicit["error"]["code"] == "policy_denied"
    broad = prepare(resumed)
    assert broad["outcome"] == "partial", json.dumps(broad)
    assert broad["data"]["coverage"]["source_observations"][source["source_id"]] == "policy_denied"
    assert "forbidden after policy change" not in json.dumps(broad)


def test_forget_during_reappearance_read_cannot_resurrect_source(resumed, monkeypatch):
    path, source = register_missing(resumed)
    path.write_bytes(b"resume: removed during reobservation\n")
    service = resumed["service"]
    original = service.sources.prepare

    def forget_after_read(item):
        prepared = original(item)
        deleted = call(
            service,
            "data_forget",
            target={"kind": "source", "source_id": source["source_id"]},
            expected_data_revision=service.store.meta()["data_revision"],
        )
        assert deleted["outcome"] == "ok"
        return prepared

    monkeypatch.setattr(service.sources, "prepare", forget_after_read)
    result = prepare(resumed)
    assert result["outcome"] == "partial"
    assert result["data"]["evidence"] == []
    assert result["data"]["coverage"]["source_observations"][source["source_id"]] == "not_found"
    assert service.db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    assert "removed during reobservation" not in json.dumps(result)
    assert prepare(resumed)["data"]["evidence"] == []


def test_missing_reobservation_stops_at_deadline_and_reports_unobserved_ids(resumed, monkeypatch):
    ids = [
        register_missing(resumed, never_present=True, name=f"missing-{i}.md")[1]["source_id"]
        for i in range(4)
    ]
    service = resumed["service"]
    clock = [0.0]
    monkeypatch.setattr("jev_context.context.time.monotonic", lambda: clock[0])
    original = service.sources.prepare
    attempted = []

    def consume_deadline(item):
        attempted.append(item["relative_path"])
        prepared = original(item)
        clock[0] = service.config.timeout_seconds + 1
        return prepared

    monkeypatch.setattr(service.sources, "prepare", consume_deadline)
    packet = prepare(resumed)
    assert packet["outcome"] == "partial"
    observations = packet["data"]["coverage"]["source_observations"]
    assert set(observations) == set(ids)
    assert len(attempted) == 1
    assert list(observations.values()).count("missing") == 1
    assert list(observations.values()).count("unverified_deadline") == 3
