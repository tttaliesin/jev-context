import json

import pytest

from jev_context.budget import bounded
from jev_context.common import response


def wire_bytes(result):
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    wire = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result,
        "isError": result["outcome"] in {"error", "conflict"},
    }
    return len(json.dumps(wire, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def test_required_evidence_that_cannot_fit_is_not_silently_dropped():
    result = bounded(
        response("r", {"evidence": [{"role": "required_evidence", "text": "필수 원문 " * 500}]}),
        1024,
        "evidence",
    )
    assert result["outcome"] == "insufficient"
    assert result["data"]["reason"] == "budget_exceeded"
    assert wire_bytes(result) <= 1024


def test_optional_evidence_is_removed_before_required_evidence():
    result = bounded(
        response(
            "r",
            {
                "evidence": [
                    {"role": "support_candidate", "text": "optional " * 500},
                    {"role": "required_evidence", "text": "must preserve"},
                ]
            },
        ),
        1024,
        "evidence",
    )
    assert result["outcome"] == "ok"
    assert result["data"]["evidence"] == [{"role": "required_evidence", "text": "must preserve"}]
    assert result["data"]["wire_budget"]["omitted_optional"] == 1
    assert wire_bytes(result) <= 1024


def test_mandatory_capability_survives_or_returns_insufficient():
    result = bounded(
        response("r", {"selected": [{"mandatory": True, "description": "required " * 500}]}),
        1024,
        "selected",
    )
    assert result["outcome"] == "insufficient"
    assert wire_bytes(result) <= 1024


@pytest.mark.parametrize("limit", [1024, 2048, 4096])
def test_small_korean_packet_preserves_scope_and_reports_wire_size(limit):
    result = bounded(
        response(
            "r",
            {
                "scope": {"constraints": ["외부 전송 금지"]},
                "evidence": [{"text": "자료", "role": "required_evidence"}],
            },
        ),
        limit,
        "evidence",
    )
    assert result["data"]["scope"]["constraints"] == ["외부 전송 금지"]
    assert result["data"]["evidence"][0]["text"] == "자료"
    assert result["data"]["wire_budget"]["used_bytes"] == wire_bytes(result)
    assert wire_bytes(result) <= limit
