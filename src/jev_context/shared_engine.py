"""Demand-started, user-local model broker shared by otherwise independent MCP hosts.

Status never starts a process or renews its idle timer. A proxy owns no model process.
Only the broker owns the worker; authenticated inference is serialized there. Discovery is
owner-only, the transport is loopback JSON, and mutual HMAC precedes any evidence payload.
"""

from __future__ import annotations

import argparse
import contextlib
import hmac
import json
import math
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from .common import DomainError, digest, dumps, strict_loads
from .engines import Laya, SemifOpenVINO, fingerprint
from .storage import FileLock, protect_directory

MAX_FRAME = 131072
PROTOCOL = 1


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        protect_directory(path)
    else:
        path.chmod(0o700)


def publish_endpoint(temporary, endpoint):
    # Windows status readers may briefly deny atomic replacement of the old file.
    # Keep the broker lock and old endpoint intact while retrying only sharing /
    # access-denied errors. Permanent permissions still fail within a fixed bound.
    deadline = time.monotonic() + 0.5
    while True:
        try:
            temporary.replace(endpoint)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Number outside bounds")
    return float(value)


def frame_read(connection, deadline):
    def read(length):
        result = bytearray()
        while len(result) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("IPC deadline")
            connection.settimeout(remaining)
            part = connection.recv(length - len(result))
            if not part:
                raise EOFError("IPC closed")
            result.extend(part)
        return result

    size = struct.unpack("!I", read(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("Invalid IPC frame size")
    value = strict_loads(read(size).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected IPC object")
    return value


def frame_write(connection, value, deadline):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_FRAME:
        raise ValueError("IPC frame too large")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("IPC deadline")
    connection.settimeout(remaining)
    connection.sendall(struct.pack("!I", len(raw)) + raw)


def proof(key, role, client, server, identity):
    return hmac.new(
        bytes.fromhex(key), f"{role}:{client}:{server}:{identity}".encode(), "sha256"
    ).hexdigest()


def identity(profile):
    return digest(dumps({"profile": profile, "fingerprint": fingerprint(profile)}).encode())


def detached_options():
    if os.name != "nt":
        return {"start_new_session": True}
    import ctypes

    in_job = ctypes.c_int()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.IsProcessInJob.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job)):
        raise OSError(ctypes.get_last_error(), "Cannot inspect process Job membership")
    # Respect the parent's Job policy: never fall back to a child whose lifetime is MCP-owned.
    flags = subprocess.CREATE_NO_WINDOW
    if in_job.value:
        flags |= subprocess.CREATE_BREAKAWAY_FROM_JOB
    return {"creationflags": flags}


class SharedLocalEngine:
    """A lazy connection, not a lease: constructing/closing it never touches a worker."""

    def __init__(self, profile):
        if profile.get("family") not in {"laya", "semif_openvino"}:
            raise DomainError("engine_unavailable", "Unsupported shared local engine")
        self.profile = dict(profile)
        self.fingerprint = fingerprint(profile)
        self.identity = identity(profile)
        self.directory = Path(profile["lock_root"]).resolve() / "shared"
        self.endpoint = self.directory / "endpoint.json"
        self.closed = False

    def _call(self, action, request=None, timeout=0.5):
        info = strict_loads(self.endpoint.read_text(encoding="utf-8"))
        if info.get("identity") != self.identity or info.get("protocol") != PROTOCOL:
            try:
                with FileLock(self.directory / "broker.lock"):
                    raise FileNotFoundError("Stale endpoint for previous profile")
            except DomainError as exc:
                raise DomainError(
                    "engine_busy", "A different model profile owns this broker"
                ) from exc
        port = info.get("port")
        if type(port) is not int or not 0 < port < 65536:
            raise ValueError("Invalid broker address")
        deadline = time.monotonic() + timeout
        request_id, nonce = secrets.token_hex(16), secrets.token_hex(32)
        with socket.create_connection(
            ("127.0.0.1", port), timeout=min(timeout, 0.25)
        ) as connection:
            frame_write(
                connection,
                {"protocol": PROTOCOL, "nonce": nonce, "identity": self.identity},
                deadline,
            )
            hello = frame_read(connection, deadline)
            server_nonce = hello["nonce"]
            expected = proof(info["key"], "server", nonce, server_nonce, self.identity)
            if not hmac.compare_digest(hello.get("proof", ""), expected):
                raise ValueError("Broker authentication failed")
            if action == "evaluate":
                remaining = int((deadline - time.monotonic()) * 1000)
                if remaining <= 0:
                    raise DomainError("judgment_deadline", "IPC exhausted the judgment budget")
                request = {**request, "deadline_ms": min(request["deadline_ms"], remaining)}
            frame_write(
                connection,
                {
                    "proof": proof(info["key"], "client", nonce, server_nonce, self.identity),
                    "request_id": request_id,
                    "action": action,
                    "request": request,
                },
                deadline,
            )
            response = frame_read(connection, deadline)
        if (
            response.get("request_id") != request_id
            or response.get("instance_id") != info["instance_id"]
        ):
            raise ValueError("Broker response identity mismatch")
        if "error" in response:
            error = response["error"]
            raise DomainError(error["code"], error["message"], True)
        return response["data"]

    def snapshot(self):
        empty = {
            "state": "idle",
            "broker_pid": None,
            "worker_pid": None,
            "instance_id": None,
            "preparation_error": None,
        }
        if self.closed:
            return {**empty, "state": "disabled"}
        try:
            return self._call("status")
        except DomainError as exc:
            return {**empty, "state": "unavailable", "preparation_error": exc.code}
        except (OSError, ValueError, KeyError, EOFError):
            return empty

    @property
    def state(self):
        return self.snapshot()["state"]

    def _start(self, *, persistent=False):
        private_directory(self.directory)
        try:
            with FileLock(self.directory / "launch.lock"):
                # The persistent broker lock proves whether a stale endpoint may be replaced.
                try:
                    with FileLock(self.directory / "broker.lock"):
                        pass
                except DomainError as exc:
                    if persistent and not self._call("status").get("persistent"):
                        raise DomainError(
                            "engine_host_mode_conflict",
                            "Existing host is transient; stop it when idle before installing a persistent host",
                        ) from exc
                    return
                profile_path = self.directory / "profile.json"
                profile_path.write_text(dumps(self.profile), encoding="utf-8")
                kwargs = detached_options()
                command = [
                    sys.executable,
                    "-m",
                    "jev_context.shared_engine",
                    "--profile",
                    str(profile_path),
                ]
                if persistent:
                    command.append("--persistent")
                with (self.directory / "broker.stderr.log").open("ab") as stderr:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=stderr,
                        cwd=Path(__file__).resolve().parents[2],
                        **kwargs,
                    )
                # Keep election until the child owns broker.lock; never wait for model loading.
                end = time.monotonic() + 0.8
                while process.poll() is None and time.monotonic() < end:
                    try:
                        if self._call("status", timeout=0.1).get("broker_pid"):
                            return
                    except (OSError, ValueError, KeyError, EOFError, DomainError):
                        pass
                    time.sleep(0.02)
                if process.poll() is not None and process.returncode != 0:
                    raise DomainError("engine_unavailable", "Shared model host failed to start")
        except DomainError as exc:
            if exc.code != "busy":
                raise
        except OSError as exc:
            raise DomainError(
                "engine_host_required",
                "Start the user-local model host outside the MCP Job; no child fallback",
                True,
            ) from exc

    def prepare(self):
        """An explicit user request to warm the shared model; never run by status reads."""
        if self.closed:
            raise DomainError("engine_unavailable", "Model connection is closed")
        try:
            return self._call("prepare", timeout=1)
        except (OSError, ValueError, KeyError, EOFError):
            self._start()
            try:
                return self._call("prepare", timeout=1)
            except (OSError, ValueError, KeyError, EOFError) as exc:
                raise DomainError(
                    "engine_preparing", "Model host is starting; retry", True
                ) from exc

    def evaluate(self, request):
        if self.closed:
            raise DomainError("engine_unavailable", "Model connection is closed")
        milliseconds = number(request.get("deadline_ms"), 1, 30000)
        if request.get("profile_fingerprint") != self.fingerprint:
            raise DomainError("engine_unavailable", "Profile fingerprint mismatch")
        try:
            raw = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
            if len(raw) > MAX_FRAME - 1024:
                raise ValueError("Oversized model request")
        except (ValueError, TypeError, RecursionError) as exc:
            raise DomainError(
                "input_incomplete", "Invalid or oversized local model request"
            ) from exc
        started = time.monotonic()
        try:
            return self._call("evaluate", request, timeout=milliseconds / 1000)
        except (OSError, ValueError, KeyError, EOFError) as exc:
            self._start()
            remaining = int(milliseconds - (time.monotonic() - started) * 1000)
            if remaining > 0:
                try:
                    return self._call(
                        "evaluate", {**request, "deadline_ms": remaining}, timeout=remaining / 1000
                    )
                except (OSError, ValueError, KeyError, EOFError):
                    pass
            raise DomainError(
                "engine_preparing", "Shared model is starting; retry a judgment when ready", True
            ) from exc

    def close(self):
        self.closed = True


class Broker:
    def __init__(self, profile):
        self.profile = profile
        self.identity = identity(profile)
        self.instance_id = secrets.token_hex(16)
        self.key = secrets.token_hex(32)
        self.engine = {"laya": Laya, "semif_openvino": SemifOpenVINO}[profile["family"]](profile)
        self.idle_seconds = number(profile.get("idle_timeout_seconds", 120), 0.1, 3600)
        self.directory = Path(profile["lock_root"]).resolve() / "shared"
        self.mutex = threading.RLock()
        self.inference = threading.Lock()
        self.slots = threading.BoundedSemaphore(8)
        self.state, self.error = "idle", None
        self.last_use = time.monotonic()
        self.active = 0
        self.stopping = False
        self.persistent = False

    def prepare(self):
        try:
            self.engine.prepare()
            with self.mutex:
                self.state, self.error, self.last_use = "shadow", None, time.monotonic()
        except Exception as exc:
            # A malformed installation must not strand the host in preparing forever.
            with contextlib.suppress(Exception):
                self.engine.close()
            with self.mutex:
                self.state = "unavailable"
                self.error = getattr(exc, "code", "engine_unavailable")
                self.last_use = time.monotonic()

    def snapshot(self):
        with self.mutex:
            process = self.engine.process
            if self.state == "shadow" and process and process.poll() is not None:
                self.state, self.error = "unavailable", "engine_worker_exited"
            return {
                "state": self.state,
                "preparation_error": self.error,
                "instance_id": self.instance_id,
                "broker_pid": os.getpid(),
                "worker_pid": process.pid if process else None,
                "active_requests": self.active,
                "idle_timeout_seconds": self.idle_seconds,
                "persistent": self.persistent,
                "startup": self.engine.startup,
            }

    def request_prepare(self):
        with self.mutex:
            if self.stopping:
                raise DomainError("engine_preparing", "Model host is stopping; retry", True)
            if self.state in {"idle", "unavailable"}:
                if self.active:
                    raise DomainError("engine_busy", "Model host has active work")
                if self.state == "unavailable":
                    self.engine.close()
                self.state, self.error = "preparing", None
                threading.Thread(target=self.prepare, daemon=True).start()
            return self.snapshot()

    def evaluate(self, request, received):
        if (
            not isinstance(request, dict)
            or request.get("profile_fingerprint") != self.engine.fingerprint
        ):
            raise DomainError("engine_unavailable", "Profile fingerprint mismatch")
        deadline = received + number(request.get("deadline_ms"), 1, 30000) / 1000
        with self.mutex:
            if self.stopping:
                raise DomainError("engine_preparing", "Idle broker is stopping; retry")
            if self.state == "idle":
                self.state = "preparing"
                threading.Thread(target=self.prepare, daemon=True).start()
            if self.state != "shadow":
                raise DomainError(self.error or "engine_preparing", "Shared model is not ready")
            self.active += 1
        acquired = False
        try:
            acquired = self.inference.acquire(timeout=max(0, deadline - time.monotonic()))
            remaining = int((deadline - time.monotonic()) * 1000)
            if not acquired or remaining <= 0:
                raise DomainError("judgment_deadline", "Shared model request deadline elapsed")
            # All queue time is charged; the worker receives only the budget still remaining.
            return self.engine.evaluate({**request, "deadline_ms": remaining})
        finally:
            if acquired:
                self.inference.release()
            with self.mutex:
                self.active -= 1
                self.last_use = time.monotonic()
                if self.engine.state != "shadow":
                    self.state, self.error = "unavailable", "engine_unavailable"

    def handle(self, connection):
        try:
            with connection:
                handshake_deadline = time.monotonic() + 1
                hello = frame_read(connection, handshake_deadline)
                nonce = hello.get("nonce")
                if (
                    hello.get("protocol") != PROTOCOL
                    or hello.get("identity") != self.identity
                    or not isinstance(nonce, str)
                    or len(nonce) != 64
                ):
                    return
                server_nonce = secrets.token_hex(32)
                frame_write(
                    connection,
                    {
                        "nonce": server_nonce,
                        "proof": proof(self.key, "server", nonce, server_nonce, self.identity),
                    },
                    handshake_deadline,
                )
                message = frame_read(connection, handshake_deadline)
                if not hmac.compare_digest(
                    message.get("proof", ""),
                    proof(self.key, "client", nonce, server_nonce, self.identity),
                ):
                    return
                received = time.monotonic()
                action, request_id = message.get("action"), message.get("request_id")
                if not isinstance(request_id, str) or len(request_id) != 32:
                    return
                response = {"request_id": request_id, "instance_id": self.instance_id}
                deadline = received + 1
                try:
                    if action == "status":
                        result = self.snapshot()
                    elif action == "prepare":
                        result = self.request_prepare()
                    elif action == "stop":
                        with self.mutex:
                            if self.active or self.state == "preparing":
                                raise DomainError("engine_busy", "Model host has active work")
                            self.stopping = True
                            result = {"stopping": True}
                    elif action == "evaluate":
                        request = message.get("request")
                        if not isinstance(request, dict):
                            raise ValueError("Expected request")
                        deadline = (
                            received + number(request.get("deadline_ms"), 1, 30000) / 1000 + 0.1
                        )
                        result = self.evaluate(request, received)
                    else:
                        raise DomainError("invalid_argument", "Unknown broker operation")
                    response["data"] = result
                except DomainError as exc:
                    response["error"] = {"code": exc.code, "message": str(exc)}
                frame_write(connection, response, deadline)
        except (OSError, ValueError, KeyError, TypeError, EOFError):
            pass  # Reject malformed/unauthenticated clients without affecting the worker.
        finally:
            self.slots.release()

    def run(self, *, persistent=False):
        self.persistent = persistent
        private_directory(self.directory)
        endpoint = self.directory / "endpoint.json"
        with FileLock(self.directory / "broker.lock"):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(8)
                listener.settimeout(0.1)
                info = {
                    "protocol": PROTOCOL,
                    "identity": self.identity,
                    "instance_id": self.instance_id,
                    "key": self.key,
                    "port": listener.getsockname()[1],
                    "broker_pid": os.getpid(),
                }
                temporary = endpoint.with_suffix(".tmp")
                temporary.write_text(dumps(info), encoding="utf-8")
                publish_endpoint(temporary, endpoint)
                try:
                    while True:
                        with self.mutex:
                            if self.stopping:
                                break
                            expired = (
                                self.state != "preparing"
                                and not self.active
                                and time.monotonic() - self.last_use >= self.idle_seconds
                            )
                            if expired:
                                if not persistent:
                                    self.stopping = True
                                    break
                                self.engine.close()
                                self.state, self.error = "idle", None
                                self.last_use = time.monotonic()
                        try:
                            connection, _ = listener.accept()
                        except TimeoutError:
                            continue
                        if not self.slots.acquire(blocking=False):
                            connection.close()
                            continue
                        threading.Thread(
                            target=self.handle, args=(connection,), daemon=True
                        ).start()
                finally:
                    self.engine.close()
                    with contextlib.suppress(OSError):
                        endpoint.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument(
        "--persistent",
        action="store_true",
        help="Keep only the lightweight listener after model idle release",
    )
    controls = parser.add_mutually_exclusive_group()
    controls.add_argument(
        "--start",
        action="store_true",
        help="Launch an independent user-local host without loading a model",
    )
    controls.add_argument("--stop", action="store_true", help="Stop the authenticated idle host")
    controls.add_argument("--status", action="store_true")
    args = parser.parse_args()
    profile = strict_loads(args.profile.read_text(encoding="utf-8"))
    if args.start or args.stop or args.status:
        proxy = SharedLocalEngine(profile)
        if args.start:
            proxy._start(persistent=args.persistent)
        result = proxy._call("stop") if args.stop else proxy.snapshot()
        if args.start and not result.get("broker_pid"):
            raise DomainError("engine_unavailable", "Model host startup was not confirmed")
        print(dumps(result))
        return
    try:
        Broker(profile).run(persistent=args.persistent)
    except DomainError as exc:
        if exc.code != "busy":
            raise


if __name__ == "__main__":
    main()
