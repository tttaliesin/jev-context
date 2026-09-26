"""Attach to an explicitly started, work-scoped Modal session over authenticated loopback."""

import http.client
import queue
import socket
import threading
import time
from pathlib import Path

from .common import DomainError, dumps, strict_loads
from .engines import convert, fingerprint, questions_wire

MAX_FRAME = 262144


def descriptor(path):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 4096:
            raise ValueError()
        value = strict_loads(raw)
        if (
            value["version"] != 1
            or value["expires_at"] <= time.time()
            or type(value["port"]) is not int
            or not 1 <= value["port"] <= 65535
            or not isinstance(value["token"], str)
            or len(value["token"]) != 64
        ):
            raise ValueError()
        return value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DomainError("engine_unavailable", "No live Modal work session") from exc


def exchange(session, path, payload, timeout):
    body = dumps(payload).encode()
    if len(body) > MAX_FRAME:
        raise DomainError("input_incomplete", "Remote request exceeds byte limit")
    connection = http.client.HTTPConnection("127.0.0.1", session["port"], timeout=timeout)

    def send():
        try:
            completed.put(_exchange(connection, session, path, body))
        except Exception as exc:
            completed.put(exc)

    completed = queue.Queue(maxsize=1)
    threading.Thread(target=send, daemon=True).start()
    try:
        result = completed.get(timeout=timeout)
    except queue.Empty as exc:
        # Bound the whole exchange, including headers and slow response bodies.
        if connection.sock:
            try:
                connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        raise DomainError("deadline_exceeded", "Modal session deadline exceeded") from exc
    if isinstance(result, Exception):
        raise result
    return result


def _exchange(connection, session, path, body):
    try:
        connection.request(
            "POST",
            path,
            body,
            {"Content-Type": "application/json", "Authorization": "Bearer " + session["token"]},
        )
        response = connection.getresponse()
        raw = response.read(MAX_FRAME + 1)
        if response.status != 200 or len(raw) > MAX_FRAME:
            raise DomainError("engine_unavailable", "Session rejected the request")
        value = strict_loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Response must be an object")
        return value
    except TimeoutError as exc:
        raise DomainError("deadline_exceeded", "Modal session deadline exceeded") from exc
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise DomainError("engine_unavailable", "Modal session is unavailable") from exc
    finally:
        connection.close()


class ModalOpenJev:
    def __init__(self, profile):
        self.profile = profile
        self.fingerprint = fingerprint(profile)
        self.state = "unavailable"

    def prepare(self):
        # Attachment is lazy: starting MCP never allocates a GPU.
        try:
            status = exchange(self._session(), "/status", {}, 1)
            if status.get("state") != "ready":
                raise DomainError("engine_unavailable", "Session is not ready")
            self.state = "shadow"
        except DomainError:
            self.state = "unavailable"

    def _session(self):
        try:
            session = descriptor(self.profile["session_file"])
        except DomainError:
            self.state = "unavailable"
            raise
        if session["profile_fingerprint"] != self.fingerprint:
            raise DomainError("engine_unavailable", "Session profile changed; start a new session")
        return session

    def evaluate(self, request):
        return self.evaluate_many([request])[0]

    def evaluate_many(self, requests):
        if not 1 <= len(requests) <= 8:
            raise DomainError("input_incomplete", "Remote batch requires 1 to 8 candidates")
        session = self._session()
        rows = []
        for request in requests:
            if (
                request["profile_fingerprint"] != self.fingerprint
                or request.get("work_id") != session["work_id"]
                or request.get("project_id") != session["project_id"]
            ):
                raise DomainError(
                    "policy_denied", "Model session belongs to another work or project"
                )
            rows.append(
                {
                    "evaluation_id": request["evaluation_id"],
                    "state": request["state"],
                    "questions": questions_wire(request["questions"]),
                }
            )
        timeout = min(2.0, min(r["deadline_ms"] for r in requests) / 1000)
        if timeout <= 0:
            raise DomainError("deadline_exceeded", "No model time remains")
        started = time.monotonic()
        payload = {
            "version": 1,
            "project_id": session["project_id"],
            "work_id": session["work_id"],
            "profile_fingerprint": self.fingerprint,
            "batch_id": requests[0]["evaluation_id"],
            "deadline_at": time.time() + timeout,
            "items": rows,
        }
        try:
            response = exchange(session, "/evaluate", payload, timeout)
            if time.monotonic() - started > timeout:
                raise DomainError(
                    "deadline_exceeded", "Response arrived after the judgment deadline"
                )
            if response.get("error"):
                raise DomainError(response["error"], "Remote judgment abstained")
            if response.get("batch_id") != payload["batch_id"] or len(response["items"]) != len(
                rows
            ):
                raise ValueError("Response batch mismatch")
            results = []
            for request, raw in zip(requests, response["items"], strict=True):
                if raw["evaluation_id"] != request["evaluation_id"]:
                    raise ValueError("Response evaluation mismatch")
                if raw.get("error"):
                    results.append({"status": "abstained", "reason": raw["error"]})
                    continue
                usage = raw["usage"]
                counts = usage["verified_read_tokens"]
                if (
                    usage.get("token_count_verified") is not True
                    or not isinstance(counts, list)
                    or not counts
                    or any(
                        type(n) is not int or not 1 <= n <= self.profile["max_input_tokens"]
                        for n in counts
                    )
                    or sum(counts) != usage["processed_input_tokens"]
                ):
                    raise ValueError("Unverified token preflight")
                results.append(
                    {
                        "evaluation_id": request["evaluation_id"],
                        "status": "observed",
                        "answers": convert(
                            raw, request["questions"], self.profile["score_definition"]
                        ),
                        "usage": raw["usage"],
                        "latency_ms": (time.monotonic() - started) * 1000,
                        "profile_fingerprint": self.fingerprint,
                    }
                )
            self.state = "shadow"
            return results
        except DomainError:
            self.state = "unavailable"
            raise
        except (KeyError, ValueError, TypeError) as exc:
            self.state = "degraded"
            raise DomainError("engine_invalid_response", "Malformed Modal result") from exc

    def close(self):
        # The explicit session owner controls GPU lifetime, independently of MCP reconnects.
        self.state = "disabled"
