import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_context.common import DomainError
from jev_context.judgment import evaluate_many, question
from jev_context.modal_engine import MAX_FRAME, ModalOpenJev, descriptor, exchange
from jev_context.session_control import control
from jev_context.storage import FileLock


@pytest.fixture
def bridge(tmp_path):
    state = {"calls": [], "delay": 0, "wrong_id": False, "verified": True}
    token = "a" * 64

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.headers.get("Authorization") != "Bearer " + token:
                self.send_error(403)
                return
            if self.path == "/status":
                output = {"state": "ready"}
            else:
                state["calls"].append(body)
                time.sleep(state["delay"])
                output = {
                    "batch_id": "wrong" if state["wrong_id"] else body["batch_id"],
                    "items": [],
                }
                for item in body["items"]:
                    answers = {}
                    for qid, q in item["questions"].items():
                        choice = next(iter(q["criteria"]))
                        answers[qid] = {
                            "choice": choice,
                            "confidence": 1.0,
                            "probabilities": {k: float(k == choice) for k in q["criteria"]},
                        }
                    output["items"].append(
                        {
                            "evaluation_id": item["evaluation_id"],
                            "answers": answers,
                            "usage": {
                                "verified_read_tokens": [100],
                                "processed_input_tokens": 100,
                                "token_count_verified": state["verified"],
                            },
                        }
                    )
            data = json.dumps(output).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                if state.get("slow_body"):
                    for byte in data:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.01)
                else:
                    self.wfile.write(data)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    profile = {
        "family": "openjev_modal",
        "model_revision": "pinned",
        "implementation_revision": "pinned",
        "precision": "fixture",
        "template_revision": "fixture",
        "sampling": {},
        "score_definition": "not correctness probability",
        "max_input_tokens": 4096,
        "session_file": str(tmp_path / "modal-session.secret.json"),
    }
    engine = ModalOpenJev(profile)
    session = {
        "version": 1,
        "port": server.server_port,
        "token": token,
        "expires_at": time.time() + 60,
        "profile_fingerprint": engine.fingerprint,
        "project_id": "p",
        "work_id": "w",
    }
    with open(profile["session_file"], "w") as file:
        json.dump(session, file)
    yield engine, session, state
    server.shutdown()
    server.server_close()


def request(engine, identifier="one", **extra):
    return {
        "evaluation_id": identifier,
        "profile_fingerprint": engine.fingerprint,
        "project_id": "p",
        "work_id": "w",
        "deadline_ms": 2000,
        "questions": [question("relevance")],
        "state": {"candidate": "원문 보존"},
        **extra,
    }


def test_batch_one_transport_with_separate_states_and_ids(bridge):
    engine, _, state = bridge
    engine.prepare()
    assert engine.state == "shadow"
    rows = engine.evaluate_many([request(engine, "one"), request(engine, "two")])
    assert [r["evaluation_id"] for r in rows] == ["one", "two"]
    assert len(state["calls"]) == 1
    assert state["calls"][0]["items"][0]["state"]["candidate"] == "원문 보존"


@pytest.mark.parametrize(
    "extra", [{"work_id": "other"}, {"project_id": "other"}, {"profile_fingerprint": "other"}]
)
def test_scope_rejected_before_remote_send(bridge, extra):
    engine, _, state = bridge
    with pytest.raises(DomainError, match="another work"):
        engine.evaluate(request(engine, **extra))
    assert not state["calls"]


@pytest.mark.parametrize("field,value", [("wrong_id", True), ("verified", False)])
def test_bad_remote_response_is_never_observed(bridge, field, value):
    engine, _, state = bridge
    state[field] = value
    with pytest.raises(DomainError) as error:
        engine.evaluate(request(engine))
    assert error.value.code == "engine_invalid_response"


def test_deadline_abstains_and_does_not_retry(bridge):
    engine, _, state = bridge
    state["delay"] = 0.15
    with pytest.raises(DomainError) as error:
        engine.evaluate(request(engine, deadline_ms=30))
    assert error.value.code == "deadline_exceeded"
    assert len(state["calls"]) == 1


def test_auth_expiry_and_frame_limit(bridge):
    engine, session, state = bridge
    with pytest.raises(DomainError):
        exchange({**session, "token": "b" * 64}, "/status", {}, 1)
    with pytest.raises(DomainError) as error:
        engine.evaluate(request(engine, state="x" * MAX_FRAME))
    assert error.value.code == "input_incomplete"
    assert not state["calls"]
    session["expires_at"] = time.time() - 1
    with open(engine.profile["session_file"], "w") as file:
        json.dump(session, file)
    with pytest.raises(DomainError):
        descriptor(engine.profile["session_file"])


def test_batch_failure_preserves_every_candidate(service, bridge):
    engine, _, state = bridge
    service.engine = engine
    service.config.engine["state"] = "shadow"
    entries = [({"candidate": "원문"}, ["relevance"])] * 3
    result = evaluate_many(service, entries, time.monotonic() + 2, work_id="other")
    assert len(result) == 3
    assert all(r["status"] == "abstained" and r["reason"] == "policy_denied" for r in result)
    assert not state["calls"]


def test_whole_exchange_deadline_includes_slow_body(bridge):
    _, session, state = bridge
    state["slow_body"] = True
    started = time.monotonic()
    with pytest.raises(DomainError) as error:
        exchange(session, "/status", {}, 0.06)
    assert error.value.code == "deadline_exceeded"
    assert time.monotonic() - started < 0.25


def test_preparing_session_can_be_seen_and_cancelled(service, bridge, tmp_path):
    engine, _, _ = bridge
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps(engine.profile), encoding="utf-8")
    service.config.engine["profile_file"] = str(profile_file)
    path = Path(engine.profile["session_file"])
    path.unlink()
    path.with_suffix(".status.json").write_text(
        json.dumps(
            {
                "state": "preparing",
                "project_id": service.config.project_id,
                "work_id": "w",
            }
        ),
        encoding="utf-8",
    )
    with FileLock(path.with_suffix(".lock")):
        assert (
            control(service.config, SimpleNamespace(command="session-status"))["state"]
            == "preparing"
        )
        assert (
            control(service.config, SimpleNamespace(command="session-stop"))["state"] == "stopping"
        )
        assert path.with_suffix(".stop").read_text() == "stop"
    assert (
        control(service.config, SimpleNamespace(command="session-status"))["state"] == "unavailable"
    )


def test_modal_credentials_excluded_even_with_broad_allowlist(service):
    service.config.allowed_paths = ("*",)
    for path in ("modal.toml", ".local/modal.toml", "modal-session.secret.json"):
        with pytest.raises(DomainError, match="Excluded file pattern"):
            service.config.check_path(path)


@pytest.mark.parametrize(
    ("engine", "profile_text", "message"),
    [
        ({"state": "disabled"}, None, "engine.profile_file"),
        ({"state": "shadow", "profile_file": "missing.json"}, None, "Cannot read"),
        ({"state": "shadow", "profile_file": "profile.json"}, "{not json", "Cannot read"),
        ({"state": "shadow", "profile_file": "profile.json"}, '{"family": "laya"}', "Modal"),
        ({"state": "shadow", "profile_file": "profile.json"}, "[]", "Modal"),
        (
            {"state": "shadow", "profile_file": "profile.json"},
            '{"family": "openjev_modal"}',
            "session_file",
        ),
    ],
)
def test_session_control_rejects_missing_or_foreign_profiles(
    service, tmp_path, engine, profile_text, message
):
    if profile_text is not None:
        (tmp_path / "profile.json").write_text(profile_text, encoding="utf-8")
    if "profile_file" in engine:
        engine = {**engine, "profile_file": str(tmp_path / engine["profile_file"])}
    service.config.engine = engine
    for command in ("session-status", "session-stop"):
        with pytest.raises(DomainError) as error:
            control(service.config, SimpleNamespace(command=command))
        assert error.value.code == "invalid_argument"
        assert message in error.value.message
