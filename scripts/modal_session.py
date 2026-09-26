"""Explicit ephemeral Modal owner with an authenticated local bridge and hard lifetime limits."""

import argparse
import hashlib
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import modal
from modal_openjev import MODEL, MODEL_REVISION, ROOT, cache, gpu_image

sys.path.insert(0, str(ROOT / "src"))
from jev_context.common import DomainError, dumps, strict_loads  # noqa: E402
from jev_context.engines import fingerprint  # noqa: E402
from jev_context.modal_engine import MAX_FRAME  # noqa: E402
from jev_context.storage import FileLock  # noqa: E402

app = modal.App("jev-openjev-work-session", include_source=False)
image = gpu_image.add_local_file(
    ROOT / "scripts/openjev_session_kernel.py", "/diagnostic/session_kernel.py"
)


@app.function(
    serialized=True,
    image=image,
    gpu="RTX-PRO-6000",
    volumes={"/cache": cache},
    cpu=(4, 4),
    memory=(65536, 65536),
    timeout=1200,
    startup_timeout=180,
    max_containers=1,
    retries=0,
    single_use_containers=True,
)
def session_worker(incoming, outgoing, binding, ttl, idle, max_tokens):
    cache.reload()
    model_path = f"/cache/hub/models--{MODEL.replace('/', '--')}/snapshots/{MODEL_REVISION}"
    process = subprocess.Popen(
        ["/opt/venv/bin/python", "/diagnostic/session_kernel.py", model_path, str(max_tokens)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "PATH": "/opt/venv/bin:" + os.environ["PATH"],
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HOME": "/cache",
        },
    )
    lines = queue.Queue()

    def read_lines():
        while line := process.stdout.readline(MAX_FRAME + 1):
            if len(line.encode()) > MAX_FRAME:
                break
            try:
                value = json.loads(line)
            except ValueError:
                continue  # Third-party startup log, never part of the protocol.
            if isinstance(value, dict) and ("status" in value or "batch_id" in value):
                lines.put(value)
        lines.put({"error": "engine_unavailable"})

    threading.Thread(target=read_lines, daemon=True).start()
    started = time.monotonic()
    seen = set()
    try:
        ready = lines.get(timeout=330)
        outgoing.put(json.dumps(ready), timeout=5)
        if ready.get("status") != "ready":
            return
        while time.monotonic() - started < ttl:
            try:
                raw = incoming.get(timeout=min(idle, ttl - (time.monotonic() - started)))
            except queue.Empty:
                return
            if raw == "stop":
                return
            if not isinstance(raw, str) or len(raw.encode()) > MAX_FRAME:
                return
            batch = json.loads(raw)
            if (
                any(batch.get(k) != v for k, v in binding.items())
                or batch["batch_id"] in seen
                or not 1 <= len(batch["items"]) <= 8
            ):
                return
            seen.add(batch["batch_id"])
            remaining = min(2, batch["deadline_at"] - time.time())
            if remaining <= 0:
                outgoing.put(
                    json.dumps({"batch_id": batch["batch_id"], "error": "deadline_exceeded"}),
                    timeout=1,
                )
                return
            process.stdin.write(json.dumps(batch, ensure_ascii=False) + "\n")
            process.stdin.flush()
            result = lines.get(timeout=remaining + 0.05)
            outgoing.put(json.dumps(result, ensure_ascii=False), timeout=1)
            if result.get("error") == "deadline_exceeded":
                return
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--ttl", type=int, default=600)
    parser.add_argument("--idle", type=int, default=90)
    args = parser.parse_args()
    if not 360 <= args.ttl <= 1100 or not 15 <= args.idle <= 180:
        parser.error("ttl must be 360..1100 seconds; idle must be 15..180 seconds")
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    for relative, expected in profile["runtime_artifacts"].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("Runtime artifact changed: regenerate and review the profile")
    dest = Path(profile["session_file"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    binding = {
        "version": 1,
        "project_id": args.project_id,
        "work_id": args.work_id,
        "profile_fingerprint": fingerprint(profile),
    }
    token = secrets.token_hex(32)
    stop = threading.Event()
    serial = threading.Lock()
    seen = set()
    expires = time.time() + args.ttl
    status_path = dest.with_suffix(".status.json")
    stop_path = dest.with_suffix(".stop")
    status = {**binding, "state": "starting", "expires_at": expires, "pid": os.getpid()}

    def save_status(state):
        status["state"] = state
        status_path.write_text(dumps(status), encoding="utf-8")

    with FileLock(dest.with_suffix(".lock"), timeout=0.1):
        stop_path.unlink(missing_ok=True)
        save_status("starting")
        try:
            with (
                modal.enable_output(),
                modal.Queue.ephemeral(environment_name="main") as incoming,
                modal.Queue.ephemeral(environment_name="main") as outgoing,
                app.run(environment_name="main"),
            ):
                status["modal_app_id"] = app.app_id
                save_status("preparing")
                call = session_worker.spawn(
                    incoming, outgoing, binding, args.ttl, args.idle, profile["max_input_tokens"]
                )
                try:
                    # Queueing for a GPU precedes model loading; keep both inside the hard TTL.
                    startup_deadline = min(expires, time.time() + 600)
                    while True:
                        if stop_path.exists():
                            status["stop_reason"] = "requested_during_preparation"
                            return
                        if time.time() >= startup_deadline:
                            status["stop_reason"] = "preparation_deadline"
                            return
                        try:
                            ready = strict_loads(outgoing.get(timeout=1))
                            break
                        except queue.Empty:
                            continue
                    if ready.get("status") != "ready":
                        status["startup_error"] = ready.get("error", "engine_unavailable")
                        raise RuntimeError("GPU preparation failed")
                    status["warmup"] = ready["warmup"]

                    class Handler(BaseHTTPRequestHandler):
                        def log_message(self, *_):
                            pass

                        def do_POST(self):
                            self.connection.settimeout(3)
                            result = {"error": "policy_denied"}
                            authorized = secrets.compare_digest(
                                self.headers.get("Authorization", ""), "Bearer " + token
                            )
                            try:
                                length = int(self.headers.get("Content-Length", "-1"))
                                if (
                                    not authorized
                                    or not 0 <= length <= MAX_FRAME
                                    or self.headers.get("Transfer-Encoding")
                                ):
                                    self.send_error(403)
                                    return
                                body = strict_loads(self.rfile.read(length))
                                if self.path == "/stop":
                                    stop.set()
                                    result = {"status": "stopping"}
                                elif self.path == "/status":
                                    result = dict(status)
                                elif self.path == "/evaluate":
                                    if any(body.get(k) != v for k, v in binding.items()):
                                        raise DomainError("policy_denied", "Wrong work session")
                                    if not 1 <= len(body["items"]) <= 8 or not serial.acquire(
                                        blocking=False
                                    ):
                                        raise DomainError("engine_busy", "One batch at a time")
                                    try:
                                        if body["batch_id"] in seen:
                                            raise DomainError(
                                                "invalid_argument", "Duplicate evaluation"
                                            )
                                        remaining = min(2, body["deadline_at"] - time.time())
                                        if (
                                            remaining <= 0
                                            or time.time() >= expires
                                            or stop.is_set()
                                        ):
                                            raise DomainError(
                                                "deadline_exceeded", "Expired evaluation"
                                            )
                                        seen.add(body["batch_id"])
                                        incoming.put(dumps(body), timeout=remaining)
                                        remaining = body["deadline_at"] - time.time()
                                        if remaining <= 0:
                                            raise queue.Empty()
                                        result = strict_loads(outgoing.get(timeout=remaining))
                                        if result.get("batch_id") != body["batch_id"]:
                                            raise ValueError("Mismatched response")
                                        if result.get("error") == "deadline_exceeded":
                                            stop.set()
                                        status["last_activity"] = time.time()
                                    except queue.Empty:
                                        stop.set()
                                        result = {"error": "deadline_exceeded"}
                                    finally:
                                        serial.release()
                            except DomainError as exc:
                                result = {"error": exc.code}
                            except (OSError, ValueError, KeyError, TypeError):
                                stop.set()
                                result = {"error": "engine_invalid_response"}
                            data = dumps(result).encode()
                            try:
                                self.send_response(200)
                                self.send_header("Content-Type", "application/json")
                                self.send_header("Content-Length", str(len(data)))
                                self.end_headers()
                                self.wfile.write(data)
                            except OSError:
                                stop.set()

                    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
                        status["last_activity"] = time.time()
                        save_status("ready")
                        session = {
                            **binding,
                            "port": server.server_port,
                            "token": token,
                            "expires_at": expires,
                        }
                        temporary = dest.with_suffix(".tmp")
                        temporary.write_text(dumps(session), encoding="utf-8")
                        if os.name != "nt":
                            temporary.chmod(0o600)
                        temporary.replace(dest)
                        threading.Thread(target=server.serve_forever, daemon=True).start()
                        print(
                            dumps(
                                {
                                    "status": "ready",
                                    "work_id": args.work_id,
                                    "modal_app_id": app.app_id,
                                }
                            ),
                            flush=True,
                        )
                        while not stop.wait(0.2):
                            if (
                                stop_path.exists()
                                or time.time() >= expires
                                or time.time() - status["last_activity"] > args.idle
                            ):
                                break
                        server.shutdown()
                finally:
                    dest.unlink(missing_ok=True)
                    stop_path.unlink(missing_ok=True)
                    call.cancel(terminate_containers=True)
        finally:
            save_status("stopped")
            print(
                dumps({"status": "stopped", "modal_app_id": status.get("modal_app_id")}), flush=True
            )


if __name__ == "__main__":
    main()
