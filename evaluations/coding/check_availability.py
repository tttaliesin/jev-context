from types import SimpleNamespace

import pytest

from jev_context import coordination
from jev_context.common import DomainError, now


def recommend(monkeypatch, items, required=(), complete=True, choice="fit"):
    evaluated = []

    def evaluate(service, entries, deadline, language, work_id):
        evaluated.extend(state["candidate"]["id"] for state, _ in entries)
        return [
            {"status": "observed", "answers": [{"choice": choice, "raw_confidence": 1.0}]}
            for _ in entries
        ]

    monkeypatch.setattr(coordination, "evaluate_many", evaluate)
    service = SimpleNamespace(
        engine=None,
        config=SimpleNamespace(engine={"state": "shadow"}),
        work=lambda _: {"goal": "read files", "scope": {"constraints": []}, "revision": 1},
    )
    result = coordination.capability_recommend(
        service,
        {
            "request_id": "test",
            "work_id": "work-test",
            "query": "read files",
            "required_ids": list(required),
            "inventory": {
                "revision": "inventory-1",
                "observed_at": now(),
                "complete": complete,
                "items": items,
            },
        },
    )
    return result, evaluated


def item(name, available=True, mandatory=False):
    return {
        "id": name,
        "kind": "tool",
        "description": "read files",
        "version": "1",
        "available": available,
        "mandatory": mandatory,
    }


def test_unavailable_required_tool_is_neither_evaluated_nor_selected(monkeypatch):
    result, evaluated = recommend(monkeypatch, [item("reader", False)], ["reader"])
    assert result["outcome"] == "insufficient"
    assert result["data"]["missing_required"] == ["reader"]
    assert result["data"]["selected"] == []
    assert evaluated == []


def test_optional_unavailable_tool_does_not_reach_inference(monkeypatch):
    result, evaluated = recommend(monkeypatch, [item("off", False), item("on", True, True)])
    assert evaluated == ["on"]
    assert [x["id"] for x in result["data"]["selected"]] == ["on"]


def test_required_available_tool_survives_model_rejection(monkeypatch):
    result, _ = recommend(monkeypatch, [item("on", True)], ["on"], choice="unfit")
    assert result["data"]["selected"][0]["id"] == "on"
    assert result["data"]["selected"][0]["mandatory"] is True


def test_missing_required_id_is_reported(monkeypatch):
    result, _ = recommend(monkeypatch, [], ["missing"])
    assert result["outcome"] == "insufficient"
    assert result["data"]["missing_required"] == ["missing"]


def test_incomplete_inventory_is_not_reported_complete(monkeypatch):
    result, _ = recommend(monkeypatch, [item("on", True, True)], complete=False)
    assert result["outcome"] == "partial"
    assert result["data"]["inventory_complete"] is False


def test_duplicate_inventory_ids_remain_invalid(monkeypatch):
    with pytest.raises(DomainError):
        recommend(monkeypatch, [item("same"), item("same")])
