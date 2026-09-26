import io
import json
from types import SimpleNamespace

from jev_context import semif_worker


def fake_model(monkeypatch):
    def prompt_ids(tokenizer, state, question, options):
        return [1] * (len(state["text"]) + len(options)), list(range(len(options)))

    def answer(infer, static, ids, slots, labels):
        return {"choice": labels[0], "probabilities": {label: 1 / len(labels) for label in labels}}

    monkeypatch.setattr(semif_worker, "prompt_ids", prompt_ids)
    monkeypatch.setattr(semif_worker, "answer", answer)


def request(evaluation_id, criteria):
    return {
        "evaluation_id": evaluation_id,
        "state_json": json.dumps({"text": "abc"}),
        "questions": {"q1": {"instructions": "?", "criteria": criteria}},
    }


def serve(monkeypatch, *lines):
    monkeypatch.setattr(
        semif_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"".join(lines)))
    )
    protocol = io.StringIO()
    semif_worker.serve(protocol, tokenizer=None, infer=None, static={})
    return [json.loads(line) for line in protocol.getvalue().splitlines()]


def test_serve_answers_and_reports_input_tokens(monkeypatch):
    fake_model(monkeypatch)
    [result] = serve(monkeypatch, json.dumps(request("e1", [["yes", "Y"], ["no", "N"]])).encode())
    assert result["evaluation_id"] == "e1"
    assert result["answers"]["q1"]["choice"] == "yes"
    assert result["usage"] == {"input_tokens": 5}


def test_invalid_options_return_an_error_and_keep_serving(monkeypatch):
    fake_model(monkeypatch)
    results = serve(
        monkeypatch,
        json.dumps(request("dup", [["a", "A"], ["a", "B"]])).encode() + b"\n",
        json.dumps(request("one", [["a", "A"]])).encode() + b"\n",
        json.dumps(request("ok", [["a", "A"], ["b", "B"]])).encode() + b"\n",
    )
    assert results[:2] == [
        {"evaluation_id": "dup", "error": "input_or_runtime_incomplete"},
        {"evaluation_id": "one", "error": "input_or_runtime_incomplete"},
    ]
    assert results[2]["answers"]["q1"]["choice"] == "a"


def test_oversized_request_line_stops_the_worker(monkeypatch):
    fake_model(monkeypatch)
    oversized = b" " * 65537 + b"\n"
    after = json.dumps(request("never", [["a", "A"], ["b", "B"]])).encode()
    assert serve(monkeypatch, oversized, after) == []
