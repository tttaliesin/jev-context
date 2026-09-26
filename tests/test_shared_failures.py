"""Process-boundary elections and recovery using only test-owned fake model runtimes."""

import contextlib
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import test_shared_engine as shared
from test_shared_engine import ask, begin_preparing, process_alive

from jev_context.common import DomainError
from jev_context.shared_engine import SharedLocalEngine

runtime_factory = shared.runtime_factory


def wait_until_dead(pid, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.04)
    return not process_alive(pid)


def terminate_test_owned_pid(runtime, pid):
    """Kill one recorded fake-runtime PID, never a process name or an entire tree."""
    assert type(pid) is int and pid > 0 and pid != os.getpid() and pid in runtime.pids
    assert (Path(runtime.profile["model_path"]) / "model.xml").read_bytes() == b"x"
    if not process_alive(pid):
        return
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateProcess.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100001, False, pid)  # SYNCHRONIZE | PROCESS_TERMINATE
        assert handle, "Could not open the test-owned process"
        try:
            assert kernel.TerminateProcess(handle, 73)
            assert kernel.WaitForSingleObject(handle, 3000) == 0
        finally:
            kernel.CloseHandle(handle)
    else:
        os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)


def crash_broker_and_check_worker_exit(runtime, engine, prepared):
    current = runtime.snapshot(engine)
    assert current["instance_id"] == prepared["instance_id"]
    assert current["broker_pid"] == prepared["broker_pid"]
    assert current["worker_pid"] == prepared["worker_pid"]
    endpoint = json.loads(engine.endpoint.read_text(encoding="utf-8"))
    assert endpoint["broker_pid"] == prepared["broker_pid"]
    assert endpoint["instance_id"] == prepared["instance_id"]
    terminate_test_owned_pid(runtime, prepared["broker_pid"])
    try:
        assert wait_until_dead(prepared["broker_pid"])
        assert wait_until_dead(prepared["worker_pid"]), "Worker survived its broker's abrupt exit"
    finally:
        # Preserve a failed assertion while preventing a faulty implementation from leaking a fake worker.
        if process_alive(prepared["worker_pid"]):
            terminate_test_owned_pid(runtime, prepared["worker_pid"])
    assert engine.endpoint.exists(), "Hard termination should leave the endpoint to be recovered"
    assert (
        json.loads(engine.endpoint.read_text(encoding="utf-8"))["instance_id"]
        == prepared["instance_id"]
    )


COLD_CLIENT = """
import json, os, sys, time
from pathlib import Path
from jev_context.common import DomainError
from jev_context.shared_engine import SharedLocalEngine
engine = SharedLocalEngine(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
gate, ident = Path(sys.argv[2]), sys.argv[3]
payload = dict(evaluation_id=ident, profile_fingerprint=engine.fingerprint,
               source_refs=[], state='설계 근거', deadline_ms=3000,
               questions=[dict(id='question-' + ident, purpose='relevance', instructions='Related?',
                               options={'relevant':'yes', 'insufficient_evidence':'unknown'},
                               language='ko')])
print(json.dumps({'ready':os.getpid()}), flush=True)
deadline = time.monotonic() + 8
while not gate.exists():
    if time.monotonic() >= deadline:
        raise AssertionError('Parent did not release the first-demand barrier')
    time.sleep(0.01)
first_code = None
try:
    while time.monotonic() < deadline:
        try:
            result = engine.evaluate(payload)
            break
        except DomainError as error:
            if first_code is None:
                first_code = error.code
            if error.code != 'engine_preparing':
                raise
            time.sleep(0.04)
    else:
        raise AssertionError('Shared model never became available')
    print(json.dumps({'result':result, 'snapshot':engine.snapshot(), 'first_code':first_code},
                     ensure_ascii=False), flush=True)
finally:
    engine.close()
"""


def test_two_python_clients_elect_one_broker_and_prepare_one_worker(runtime_factory, monkeypatch):
    monkeypatch.setenv("FAKE_COMPILE_SECONDS", "0.5")
    runtime = runtime_factory(idle_timeout_seconds=2)
    directory = Path(runtime.profile["model_path"]).parent
    profile_path, gate = directory / "concurrent-profile.json", directory / "start.flag"
    profile_path.write_text(json.dumps(runtime.profile), encoding="utf-8")
    clients, receipts = [], []
    try:
        for index in range(2):
            clients.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-X",
                        "utf8",
                        "-c",
                        COLD_CLIENT,
                        str(profile_path),
                        str(gate),
                        f"client-{index}",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=dict(os.environ),
                )
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            waiting = [pool.submit(child.stdout.readline) for child in clients]
            ready = [json.loads(future.result(timeout=8)) for future in waiting]
        assert ready[0]["ready"] != ready[1]["ready"]
        gate.write_text("start", encoding="ascii")
        for child in clients:
            stdout, stderr = child.communicate(timeout=12)
            assert child.returncode == 0, stderr
            receipt = json.loads(stdout)
            receipts.append(receipt)
            runtime.pids.update(receipt["snapshot"][key] for key in ("broker_pid", "worker_pid"))
    finally:
        for child in clients:
            if child.poll() is None:
                child.kill()  # Only this test's original Popen owner.
            child.communicate(timeout=3)
            for stream in (child.stdout, child.stderr):
                stream.close()
    observer = runtime.proxy()
    prepared = runtime.wait_for(observer, "shadow")
    assert any(receipt["first_code"] == "engine_preparing" for receipt in receipts)
    for index, receipt in enumerate(receipts):
        assert receipt["first_code"] in {None, "engine_preparing"}
        assert receipt["result"]["evaluation_id"] == f"client-{index}"
        assert receipt["result"]["answers"][0]["question_id"] == f"question-client-{index}"
        for key in ("instance_id", "broker_pid", "worker_pid"):
            assert receipt["snapshot"][key] == prepared[key]
    lifecycle = [
        json.loads(line)
        for line in (Path(runtime.profile["lock_root"]) / "engine.log")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert sum(event["event"] == "ready" for event in lifecycle) == 1
    assert sum(event["event"] == "prepare_start" for event in lifecycle) == 1


def test_crashed_broker_releases_worker_and_same_profile_recovers_stale_endpoint(runtime_factory):
    runtime = runtime_factory(idle_timeout_seconds=2)
    engine = runtime.proxy()
    begin_preparing(runtime, engine)
    prepared = runtime.wait_for(engine, "shadow")
    assert engine.evaluate(ask(engine, "before-crash"))["status"] == "observed"
    crash_broker_and_check_worker_exit(runtime, engine, prepared)
    assert engine.state == "idle"
    begin_preparing(runtime, engine)
    recovered = runtime.wait_for(engine, "shadow")
    assert recovered["instance_id"] != prepared["instance_id"]
    assert engine.evaluate(ask(engine, "after-crash"))["evaluation_id"] == "after-crash"


def test_different_profile_is_busy_while_owner_lives_but_recovers_dead_endpoint(runtime_factory):
    runtime = runtime_factory(idle_timeout_seconds=2)
    original = runtime.proxy()
    begin_preparing(runtime, original)
    prepared = runtime.wait_for(original, "shadow")
    alternate = SharedLocalEngine(
        {
            **runtime.profile,
            "model_revision": runtime.profile["model_revision"] + "-different",
        }
    )
    runtime.proxies.append(alternate)
    assert alternate.fingerprint != original.fingerprint
    with pytest.raises(DomainError) as conflict:
        alternate.evaluate(ask(alternate, "wrong-profile"))
    assert conflict.value.code == "engine_busy"
    unchanged = runtime.snapshot(original)
    assert unchanged["instance_id"] == prepared["instance_id"]
    assert original.evaluate(ask(original, "still-original"))["status"] == "observed"
    crash_broker_and_check_worker_exit(runtime, original, prepared)
    # The old endpoint now belongs to a dead, incompatible profile, not a live owner.
    begin_preparing(runtime, alternate)
    recovered = runtime.wait_for(alternate, "shadow")
    assert recovered["instance_id"] != prepared["instance_id"]
    result = alternate.evaluate(ask(alternate, "new-profile"))
    assert result["status"] == "observed"
    assert result["profile_fingerprint"] == alternate.fingerprint
    with pytest.raises(DomainError) as conflict:
        original.evaluate(ask(original, "old-profile"))
    assert conflict.value.code == "engine_busy"
