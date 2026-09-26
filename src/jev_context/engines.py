"""Optional local engine adapters. Importing this module never loads model weights."""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import ipaddress
import json
import math
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .common import DomainError, digest, dumps, strict_loads
from .storage import FileLock

MAX_RESPONSE = 65536
PURPOSES = {"relevance", "evidence_relation", "skill_fit", "capability_fit", "presentation"}


def fingerprint(profile):
    required = {
        "family",
        "model_revision",
        "implementation_revision",
        "precision",
        "template_revision",
        "sampling",
        "score_definition",
    }
    if not required <= profile.keys():
        raise DomainError("engine_unavailable", "Profile lacks reproducibility fields")
    adapter = {
        name: digest(Path(__file__).with_name(name).read_bytes())
        for name in (
            "engines.py",
            "laya_worker.py",
            "semif_worker.py",
            "judgment.py",
            "modal_engine.py",
            "shared_engine.py",
            "worker_lifetime.py",
            "openvino_cache.py",
        )
    }
    return digest(
        dumps(
            {
                "adapter_hashes": adapter,
                "profile": {
                    k: v
                    for k, v in profile.items()
                    if k
                    not in {
                        "endpoint",
                        "python",
                        "worker",
                        "model_path",
                        "lock_root",
                        "cache_dir",
                        "session_file",
                        "modal_python",
                        "modal_config_file",
                    }
                },
            }
        ).encode()
    )


def questions_wire(questions):
    result = {}
    for question in questions:
        if question["purpose"] not in PURPOSES or question["id"] in result:
            raise DomainError("invalid_argument", "Invalid purpose or duplicate question ID")
        options = question["options"]
        if (
            not isinstance(options, dict)
            or not 2 <= len(options) <= 32
            or "insufficient_evidence" not in options
        ):
            raise DomainError(
                "invalid_argument", "Questions require an insufficient_evidence option"
            )
        result[question["id"]] = dict(
            type="choice", instructions=question["instructions"], criteria=options
        )
    if not 1 <= len(result) <= 8:
        raise DomainError("input_incomplete", "Model batch must contain 1 to 8 questions")
    return result


def convert(raw, questions, score_definition):
    if not isinstance(raw, dict):
        raise DomainError("engine_invalid_response", "Model response must be an object")
    answers = raw.get("answers")
    if not isinstance(answers, dict) or set(answers) != {q["id"] for q in questions}:
        raise DomainError("engine_invalid_response", "Question IDs do not match")
    output = []
    for question in questions:
        answer = answers[question["id"]]
        if not isinstance(answer, dict):
            raise DomainError("engine_invalid_response", "Answer must be an object")
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict) or set(probabilities) != set(question["options"]):
            raise DomainError("engine_invalid_response", "Distribution labels do not match")
        values = list(probabilities.values())
        if any(
            type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 1 for v in values
        ):
            raise DomainError("engine_invalid_response", "Invalid distribution values")
        if not math.isclose(sum(values), 1, abs_tol=max(0.001, len(values) * 0.000051)):
            raise DomainError("engine_invalid_response", "Distribution does not sum to one")
        choice, confidence = answer.get("choice"), answer.get("confidence")
        if (
            choice not in probabilities
            or type(confidence) not in {int, float}
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
            or probabilities[choice] < max(values) - 0.0002
        ):
            raise DomainError("engine_invalid_response", "Invalid choice or confidence")
        output.append(
            dict(
                question_id=question["id"],
                choice=choice,
                raw_distribution=probabilities,
                raw_confidence=confidence,
                score_definition=score_definition,
                abstention_reason="insufficient_evidence"
                if choice == "insufficient_evidence"
                else None,
            )
        )
    return output


class OpenJev:
    def __init__(self, profile, token_counter=None):
        self.profile = profile
        self.fingerprint = fingerprint(profile)
        self.token_counter = token_counter
        address = urlsplit(profile["endpoint"])
        try:
            local = ipaddress.ip_address(address.hostname).is_loopback
        except ValueError:
            local = False
        if (
            address.scheme != "http"
            or not local
            or address.username
            or address.password
            or address.query
            or address.fragment
            or address.path not in {"", "/"}
        ):
            raise DomainError(
                "policy_denied", "Engine endpoint must be a fixed literal HTTP loopback address"
            )
        self.host, self.port = address.hostname, address.port or 80
        self.state = "unavailable"

    def prepare(self):
        # Preparation is explicit, outside normal context latency budgets.
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        try:
            connection.request("GET", "/v1/models")
            result = connection.getresponse()
            if result.status != 200 or len(result.read(MAX_RESPONSE + 1)) > MAX_RESPONSE:
                raise DomainError("engine_unavailable", "OpenJev readiness failed")
            self.state = "shadow"
        except OSError as exc:
            self.state = "unavailable"
            raise DomainError("engine_unavailable", "Local engine is unavailable") from exc
        finally:
            connection.close()

    def evaluate(self, request):
        if self.state != "shadow":
            raise DomainError("engine_unavailable", "Engine requires explicit preparation")
        if request["profile_fingerprint"] != self.fingerprint:
            raise DomainError("engine_unavailable", "Profile fingerprint mismatch")
        questions = questions_wire(request["questions"])
        if self.token_counter is None:
            raise DomainError(
                "input_incomplete", "Exact packed-input tokenizer preflight is unavailable"
            )
        payload = dict(
            model=self.profile["model_revision"], state=request["state"], questions=questions
        )
        tokens = self.token_counter(payload)
        if (
            type(tokens) is not int
            or tokens < 1
            or tokens > self.profile.get("max_input_tokens", 1024)
        ):
            raise DomainError("input_incomplete", "Packed model input exceeds profile budget")
        started = time.monotonic()
        timeout = min(2.0, request["deadline_ms"] / 1000)
        if timeout <= 0:
            raise DomainError("deadline_exceeded", "No model time remains")
        completed = queue.Queue(maxsize=1)

        def send():
            connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
            try:
                connection.request(
                    "POST",
                    "/v1/systemone",
                    dumps(payload).encode(),
                    {"Content-Type": "application/json"},
                )
                result = connection.getresponse()
                # http.client does not follow redirects or proxy environment variables.
                if result.status != 200:
                    raise DomainError(
                        "engine_unavailable", "Local engine returned a non-success status"
                    )
                body = result.read(MAX_RESPONSE + 1)
                if len(body) > MAX_RESPONSE:
                    raise DomainError(
                        "engine_invalid_response", "Model response exceeds byte limit"
                    )
                completed.put(strict_loads(body))
            except Exception as exc:
                completed.put(exc)
            finally:
                connection.close()

        threading.Thread(target=send, daemon=True).start()
        try:
            raw = completed.get(timeout=timeout)
        except queue.Empty as exc:
            self.state = "degraded"
            raise DomainError(
                "deadline_exceeded", "Engine timed out; re-prepare only after server is idle"
            ) from exc
        if isinstance(raw, Exception):
            self.state = "degraded"
            raise DomainError("engine_unavailable", "Local model request failed") from raw
        answers = convert(raw, request["questions"], self.profile["score_definition"])
        return dict(
            evaluation_id=request["evaluation_id"],
            status="observed",
            answers=answers,
            usage=raw.get("usage", {}),
            latency_ms=(time.monotonic() - started) * 1000,
            profile_fingerprint=self.fingerprint,
        )

    def close(self):
        self.state = "disabled"


class Laya:
    """A resident worker process in a separate Python environment, speaking JSON lines."""

    WORKER, NAME = "laya_worker.py", "Laya"

    def worker_request(self, request):
        return dict(
            evaluation_id=request["evaluation_id"],
            state=request["state"],
            questions=questions_wire(request["questions"]),
        )

    def __init__(self, profile):
        self.profile = profile
        self.fingerprint = fingerprint(profile)
        self.state, self.process, self.lock = "unavailable", None, None
        self.responses = queue.Queue(maxsize=2)
        # Seconds per question, reported by a warmed-up worker and updated from real requests.
        self.question_seconds = None
        self.startup = None
        self.worker_job = None

    def log(self, event, **fields):
        # Worker lifecycle record next to the model lock; MCP stdout is the protocol channel.
        try:
            path = Path(self.profile["lock_root"]) / "engine.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "host_pid": os.getpid()}
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps({**record, "event": event, **fields}) + "\n")
        except OSError:
            pass

    def prepare(self):
        preparation_started = time.monotonic()
        self.startup = None
        model_path = Path(self.profile["model_path"])
        executable = Path(self.profile["python"])
        if (
            not model_path.is_absolute()
            or not model_path.is_dir()
            or not executable.is_absolute()
            or not executable.is_file()
        ):
            raise DomainError(
                "engine_unavailable",
                f"{self.NAME} requires an existing local model and dedicated Python environment",
            )
        if self.profile.get("manifest_sha256"):
            manifest_bytes = (model_path / "manifest.json").read_bytes()
            if digest(manifest_bytes) != self.profile["manifest_sha256"]:
                raise DomainError("engine_unavailable", "Prepared model manifest changed")
            manifest = strict_loads(manifest_bytes)
            for relative, expected in manifest["files"].items():
                target = (model_path / relative).resolve(strict=True)
                if not target.is_relative_to(model_path.resolve()):
                    raise DomainError("engine_unavailable", "Invalid model manifest path")
                with target.open("rb") as source:
                    actual = hashlib.file_digest(source, "sha256").hexdigest()
                if actual != expected:
                    raise DomainError("engine_unavailable", "Prepared model bytes changed")
        manifest_seconds = time.monotonic() - preparation_started
        lock_path = Path(self.profile["lock_root"]) / "resident.lock"
        self.lock = FileLock(lock_path.with_suffix(".startup.lock"))
        try:
            self.lock.__enter__()
            self.log("prepare_start")
            stderr_path = lock_path.with_name("worker.stderr.log")
            with contextlib.suppress(OSError):  # may still be open in an older worker
                if stderr_path.stat().st_size > 1024 * 1024:
                    stderr_path.unlink()
            from .worker_lifetime import create_job

            self.worker_job, job_name = create_job()
            environment = {
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "JEV_CPU_THREADS": str(self.profile.get("cpu_threads", "")),
                "JEV_WARMUP": "1" if self.profile.get("warmup", False) else "0",
                "JEV_OV_CACHE": str(self.profile.get("cache_dir", "")),
                "JEV_WORKER_JOB": job_name,
                "JEV_OWNER_PID": str(os.getpid()),
            }
            with stderr_path.open("ab") as stderr:
                self.process = subprocess.Popen(
                    [
                        str(executable),
                        "-u",
                        str(Path(__file__).with_name(self.WORKER)),
                        str(model_path),
                        self.profile.get("device", "cpu"),
                        str(lock_path),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )

            process = self.process
            responses = self.responses = queue.Queue(maxsize=2)

            def reader():
                try:
                    while process and process.stdout:
                        line = process.stdout.readline(MAX_RESPONSE + 1)
                        if not line:
                            break
                        responses.put(line)
                except (OSError, ValueError):
                    return

            threading.Thread(target=reader, daemon=True).start()
            ready = strict_loads(
                self.responses.get(timeout=self.profile.get("prepare_timeout_seconds", 30))
            )
            if ready.get("status") == "busy":
                raise DomainError("engine_busy", f"Another {self.NAME} worker owns this profile")
            if ready.get("status") != "ready":
                raise DomainError(
                    "engine_unavailable",
                    f"{self.NAME} worker preparation failed ({ready.get('reason', 'unknown')})",
                )
            self.question_seconds = ready.get("question_seconds")
            self.startup = {
                **ready.get("startup", {}),
                "manifest_verify_seconds": manifest_seconds,
                "total_seconds": time.monotonic() - preparation_started,
            }
            self.log(
                "ready",
                worker_pid=self.process.pid,
                question_seconds=self.question_seconds,
                startup=self.startup,
            )
            self.state = "shadow"
            self.lock.__exit__(None, None, None)
            self.lock = None
        except Exception as exc:
            self.log("prepare_failed", error=getattr(exc, "code", type(exc).__name__))
            self.close()
            if isinstance(exc, DomainError) and exc.code == "busy":
                # Another host is starting this profile (Codex Desktop can run two servers).
                raise DomainError("engine_busy", f"Another {self.NAME} host is starting") from exc
            if isinstance(exc, DomainError):
                raise
            raise DomainError(
                "engine_unavailable", f"{self.NAME} worker did not become ready"
            ) from exc

    def evaluate(self, request):
        if self.state != "shadow" or not self.process:
            raise DomainError("engine_unavailable", f"{self.NAME} requires explicit preparation")
        if request["profile_fingerprint"] != self.fingerprint:
            raise DomainError("engine_unavailable", "Profile fingerprint mismatch")
        payload = dumps(self.worker_request(request)).encode() + b"\n"
        if len(payload) > MAX_RESPONSE:
            raise DomainError("input_incomplete", f"{self.NAME} request is too large")
        # deadline_ms is already bounded by the configured judgment budget. A fixed 2 s cap per
        # request refused every two-question request once a question took over ~0.9 s (V1 demo).
        timeout = max(0.001, request["deadline_ms"] / 1000)
        questions = len(request["questions"])
        if self.question_seconds and questions * self.question_seconds * 1.1 > timeout:
            # Work that cannot finish in time would end in a timeout, and a timeout stops the
            # worker because its late answer would desynchronize the protocol. Skip it instead.
            raise DomainError("judgment_deadline", f"Too little time left for {self.NAME}")
        start = time.monotonic()
        try:
            self.process.stdin.write(payload)
            self.process.stdin.flush()
            line = self.responses.get(timeout=timeout)
            if len(line) > MAX_RESPONSE:
                raise ValueError("oversized")
            raw = strict_loads(line)
            if raw.get("evaluation_id") != request["evaluation_id"]:
                raise ValueError("mismatched evaluation")
            if raw.get("error"):
                raise DomainError(
                    "input_incomplete", f"{self.NAME} rejected incomplete input or runtime changed"
                )
            latency = time.monotonic() - start
            if questions:
                self.question_seconds = latency / questions
            return dict(
                evaluation_id=request["evaluation_id"],
                status="observed",
                answers=convert(raw, request["questions"], self.profile["score_definition"]),
                usage=raw.get("usage", {}),
                latency_ms=latency * 1000,
                profile_fingerprint=self.fingerprint,
            )
        except DomainError:
            raise
        except Exception as exc:
            self.close()
            self.state = "degraded"
            raise DomainError(
                "engine_unavailable", f"{self.NAME} failed or timed out; worker stopped"
            ) from exc

    def close(self):
        if self.worker_job:
            self.worker_job.Close()
            self.worker_job = None
        if self.process:
            self.log("close", worker_pid=self.process.pid, worker_exit=self.process.poll())
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            for stream in (self.process.stdin, self.process.stdout):
                stream.close()
            self.process = None
        if self.lock and self.lock.file:
            self.lock.__exit__(None, None, None)
        self.lock = None
        self.state = "disabled"


class SemifOpenVINO(Laya):
    """SemIf direct option readout from an OpenVINO IR, run by semif_worker.py.

    The profile's python must have openvino and transformers; model_path is a fixed-length IR
    directory (model.xml/.bin, static.json, tokenizer). Same resident-worker protocol, lock,
    manifest check and 2-second request deadline as Laya.
    """

    WORKER, NAME = "semif_worker.py", "SemIf OpenVINO"

    def worker_request(self, request):
        """Keep option and state order: dumps() sorts keys, and option letters follow order.

        The option-logit readout is position-sensitive (the Korean diagnostic dropped from
        28/30 to 24/30 when options arrived alphabetically), so options travel as an ordered
        list and the state as a JSON string in its original key order.
        """
        wire = questions_wire(request["questions"])
        for question in wire.values():
            question["criteria"] = [[label, text] for label, text in question["criteria"].items()]
        return dict(
            evaluation_id=request["evaluation_id"],
            state_json=json.dumps(request["state"], ensure_ascii=False),
            questions=wire,
        )
