"""Shared local workers survive short clients, but never status-only idle traffic."""

import ctypes
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_engines import request, semif_fixture

from jev_context.common import DomainError
from jev_context.shared_engine import SharedLocalEngine


def process_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) can terminate a process on Windows; inspect a handle instead.
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, no termination right
        if not handle:
            assert ctypes.get_last_error() == 87, "Could not inspect a test-owned process"
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class Runtime:
    def __init__(self, profile):
        self.profile, self.proxies, self.pids = profile, [], set()

    def proxy(self):
        engine = SharedLocalEngine(self.profile)
        self.proxies.append(engine)
        return engine

    def snapshot(self, engine):
        result = engine.snapshot()
        assert {
            "state",
            "broker_pid",
            "worker_pid",
            "instance_id",
            "preparation_error",
        } <= result.keys()
        self.pids.update(result[key] for key in ("broker_pid", "worker_pid") if result.get(key))
        return result

    def wait_for(self, engine, state, timeout=10):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.snapshot(engine)
            if last["state"] == state:
                return last
            time.sleep(0.04)
        pytest.fail(f"Shared engine never reached {state}: {last}")

    def collect_logged_workers(self):
        log = Path(self.profile["lock_root"]) / "engine.log"
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    pid = json.loads(line).get("worker_pid")
                except ValueError:
                    continue
                if pid:
                    self.pids.add(pid)

    def drain(self):
        for proxy in self.proxies:
            proxy.close()
        observer = SharedLocalEngine(self.profile)
        deadline = time.monotonic() + self.profile["prepare_timeout_seconds"] + 5
        try:
            while time.monotonic() < deadline:
                snapshot = self.snapshot(observer)
                self.collect_logged_workers()
                if snapshot["state"] in {"idle", "unavailable"} and not any(
                    process_alive(pid) for pid in self.pids
                ):
                    return
                time.sleep(0.05)
            pytest.fail(
                f"Test runtime did not stop itself after idle timeout: {snapshot}, {self.pids}"
            )
        finally:
            observer.close()


@pytest.fixture
def runtime_factory(tmp_path, monkeypatch):
    runtimes = []

    def create(*, model_ok=True, **overrides):
        directory = tmp_path / f"rt{len(runtimes)}"
        directory.mkdir()
        config = semif_fixture(directory, monkeypatch, model_ok=model_ok)
        # Both a detached broker and its fake model worker need these import roots.
        monkeypatch.setenv(
            "PYTHONPATH",
            os.pathsep.join(
                (str(directory / "fakes"), str(Path(__file__).resolve().parents[1] / "src"))
            ),
        )
        profile = json.loads(Path(config.engine["profile_file"]).read_text(encoding="utf-8"))
        profile.update({"idle_timeout_seconds": 1.2, "prepare_timeout_seconds": 4, **overrides})
        runtime = Runtime(profile)
        runtimes.append(runtime)
        return runtime

    yield create
    for runtime in runtimes:
        runtime.drain()


def begin_preparing(runtime, engine):
    started = time.monotonic()
    with pytest.raises(DomainError) as error:
        engine.evaluate(request(engine))
    assert error.value.code == "engine_preparing"
    assert time.monotonic() - started < 1, "First use waited for model compilation"
    snapshot = runtime.snapshot(engine)
    assert snapshot["state"] in {"preparing", "shadow", "unavailable"}
    return snapshot


def ask(engine, ident, deadline_ms=3000):
    value = request(engine)
    value.update(evaluation_id=ident, deadline_ms=deadline_ms)
    value["questions"] = [{**value["questions"][0], "id": "question-" + ident}]
    return value


def test_status_and_close_do_not_start_a_broker(runtime_factory):
    runtime = runtime_factory()
    first, second = runtime.proxy(), runtime.proxy()
    for _ in range(4):
        for engine in (first, second):
            assert engine.state == "idle"
            snapshot = runtime.snapshot(engine)
            assert snapshot["state"] == "idle"
            assert snapshot["broker_pid"] is None and snapshot["worker_pid"] is None
        time.sleep(0.04)
    first.close()
    assert first.state == "disabled"
    assert second.state == "idle"
    assert not runtime.pids


def test_explicit_prepare_is_nonblocking_shared_and_status_still_releases(
    runtime_factory, monkeypatch
):
    monkeypatch.setenv("FAKE_COMPILE_SECONDS", "0.7")
    runtime = runtime_factory(idle_timeout_seconds=0.8)
    first, second = runtime.proxy(), runtime.proxy()
    assert first.snapshot()["worker_pid"] is None
    started = time.monotonic()
    state = first.prepare()
    assert state["state"] in {"preparing", "shadow"}
    assert time.monotonic() - started < 1.5
    assert second.prepare()["instance_id"] == state["instance_id"]
    prepared = runtime.wait_for(first, "shadow")
    assert second.prepare()["worker_pid"] == prepared["worker_pid"]
    first.close()
    assert second.evaluate(ask(second, "explicit-prepare"))["status"] == "observed"
    runtime.wait_for(second, "idle", timeout=4)
    assert second.snapshot()["worker_pid"] is None
    with pytest.raises(DomainError, match="closed"):
        first.prepare()


def test_first_use_is_nonblocking_and_two_proxies_share_worker_after_close(
    runtime_factory, monkeypatch
):
    monkeypatch.setenv("FAKE_COMPILE_SECONDS", "1.3")
    runtime = runtime_factory()
    first, second = runtime.proxy(), runtime.proxy()
    begin_preparing(runtime, first)
    prepared = runtime.wait_for(first, "shadow")
    assert prepared["instance_id"]
    assert process_alive(prepared["broker_pid"]) and process_alive(prepared["worker_pid"])
    assert first.evaluate(ask(first, "first"))["status"] == "observed"
    assert second.evaluate(ask(second, "second"))["status"] == "observed"
    reused = runtime.snapshot(second)
    for key in ("instance_id", "broker_pid", "worker_pid"):
        assert reused[key] == prepared[key]
    first.close()
    assert first.state == "disabled"
    assert second.state == "shadow"
    assert second.evaluate(ask(second, "after-close"))["evaluation_id"] == "after-close"
    assert runtime.snapshot(second)["worker_pid"] == prepared["worker_pid"]


def test_concurrent_clients_keep_evaluation_and_question_responses_matched(
    runtime_factory, monkeypatch
):
    monkeypatch.setenv("FAKE_INFER_SECONDS", "0.06")
    runtime = runtime_factory(idle_timeout_seconds=2)
    first, second = runtime.proxy(), runtime.proxy()
    begin_preparing(runtime, first)
    prepared = runtime.wait_for(first, "shadow")
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [
            (f"parallel-{index}", pool.submit(engine.evaluate, ask(engine, f"parallel-{index}")))
            for index, engine in enumerate((first, second, first, second))
        ]
        for ident, future in pending:
            result = future.result(timeout=8)
            assert result["status"] == "observed" and result["evaluation_id"] == ident
            assert [answer["question_id"] for answer in result["answers"]] == ["question-" + ident]
    assert runtime.snapshot(first)["instance_id"] == prepared["instance_id"]
    assert runtime.snapshot(second)["worker_pid"] == prepared["worker_pid"]


def test_one_shot_python_client_does_not_take_its_worker_down(runtime_factory, monkeypatch):
    monkeypatch.setenv("FAKE_COMPILE_SECONDS", "1")
    runtime = runtime_factory(idle_timeout_seconds=2)
    observer = runtime.proxy()
    script = """
import json, sys
from jev_context.common import DomainError
from jev_context.shared_engine import SharedLocalEngine
engine = SharedLocalEngine(json.load(sys.stdin))
payload = dict(evaluation_id='short-client', profile_fingerprint=engine.fingerprint,
               source_refs=[], state='Korean evidence', deadline_ms=1000,
               questions=[dict(id='q1', purpose='relevance', instructions='Relevant?',
                               options={'relevant':'yes', 'insufficient_evidence':'unknown'},
                               language='ko')])
try:
    engine.evaluate(payload)
except DomainError as error:
    print(json.dumps({'code':error.code, 'snapshot':engine.snapshot()}), flush=True)
else:
    raise AssertionError('First use unexpectedly waited for preparation')
engine.close()
"""
    child = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", script],
        input=json.dumps(runtime.profile),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=8,
        env=dict(os.environ),
    )
    assert child.returncode == 0, child.stderr
    receipt = json.loads(child.stdout)
    assert receipt["code"] == "engine_preparing"
    runtime.pids.update(
        receipt["snapshot"][key]
        for key in ("broker_pid", "worker_pid")
        if receipt["snapshot"].get(key)
    )
    prepared = runtime.wait_for(observer, "shadow")  # Polling may not start a replacement.
    assert observer.evaluate(ask(observer, "longer-client"))["status"] == "observed"
    if receipt["snapshot"].get("instance_id"):
        assert prepared["instance_id"] == receipt["snapshot"]["instance_id"]


def test_status_polling_does_not_extend_idle_ttl_or_restart_worker(runtime_factory):
    runtime = runtime_factory(idle_timeout_seconds=0.8)
    engine = runtime.proxy()
    begin_preparing(runtime, engine)
    prepared = runtime.wait_for(engine, "shadow")
    assert engine.evaluate(ask(engine, "last-use"))["status"] == "observed"
    started = time.monotonic()
    while time.monotonic() - started < 3:
        assert engine.state in {"shadow", "idle"}
        snapshot = runtime.snapshot(engine)
        if (
            snapshot["state"] == "idle"
            and not process_alive(prepared["broker_pid"])
            and not process_alive(prepared["worker_pid"])
        ):
            break
        time.sleep(0.04)
    else:
        pytest.fail("Status-only traffic kept the shared worker alive")
    for _ in range(4):
        assert engine.state == "idle"
        assert runtime.snapshot(engine)["state"] == "idle"
        assert not process_alive(prepared["broker_pid"])
        assert not process_alive(prepared["worker_pid"])
        time.sleep(0.04)


@pytest.mark.parametrize("failure", ["missing-assets", "compile-timeout"])
def test_preparation_failure_is_bounded_and_reported_without_retry_loop(
    runtime_factory, monkeypatch, failure
):
    if failure == "compile-timeout":
        monkeypatch.setenv("FAKE_COMPILE_SECONDS", "8")
    runtime = runtime_factory(model_ok=failure != "missing-assets", prepare_timeout_seconds=0.6)
    engine = runtime.proxy()
    started = time.monotonic()
    begin_preparing(runtime, engine)
    failed = runtime.wait_for(engine, "unavailable", timeout=5)
    assert time.monotonic() - started < 5
    assert failed["preparation_error"]
    before = failed["instance_id"]
    for _ in range(2):
        with pytest.raises(DomainError) as error:
            engine.evaluate(request(engine))
        assert error.value.code == "engine_unavailable"
        assert runtime.snapshot(engine)["instance_id"] == before
