"""Freeze and measure nine independent resumptions of one existing repair task.

The three conditions differ only in their supplied context. This small, local
comparison is not a promotion gate or evidence of a general model benefit.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ORDERS = (("e0", "e1", "e2"), ("e1", "e2", "e0"), ("e2", "e0", "e1"))
DISABLED_MCP = ("jev_context", "node_repl")
DISABLED_FEATURES = ("hooks", "memories", "multi_agent")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): sha(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def changes(before, after):
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


def relative_file(value, root=None):
    """Reject ambiguous descriptor paths and traversal before copying anything."""
    root = ROOT if root is None else root
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise ValueError(f"Expected a repository-relative file: {value}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f"Missing or external source file: {value}")
    return resolved


def context_status(value):
    found = []
    if isinstance(value, dict):
        if isinstance(value.get("judgment"), dict):
            found.append({key: value["judgment"].get(key) for key in ("status", "mode", "reason")})
        for item in value.values():
            found.extend(context_status(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(context_status(item))
    return found


def test_environment(trial):
    return {
        **os.environ,
        "PYTHONPATH": str(trial / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ADDOPTS": "",
    }


def config_path_key(value):
    return os.path.normcase(str(Path(value).resolve()))


def automatic_trust_paths(trial_paths):
    path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    existing = {config_path_key(key) for key in config.get("projects", {})}
    # Existing trust decisions remain part of the effective configuration.
    return [str(path) for path in trial_paths if config_path_key(path) not in existing]


def user_config_observation(auto_trust_paths=()):
    path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    permitted = {config_path_key(value) for value in auto_trust_paths}
    projects = config.get("projects", {})
    for key in list(projects):
        if config_path_key(key) in permitted and projects[key] == {"trust_level": "trusted"}:
            del projects[key]
    if "projects" in config and not projects:
        del config["projects"]
    semantic_bytes = json.dumps(
        config,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=lambda value: {"toml_type": type(value).__name__, "value": value.isoformat()},
    ).encode("utf-8")
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "path": str(path),
        "sha256": sha(path) if path.is_file() else None,
        "effective_sha256": hashlib.sha256(semantic_bytes).hexdigest(),
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        if path.is_file()
        else None,
    }


def configured_disabled_mcp():
    path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    # An enabled=false entry for a nonexistent server is invalid in this CLI.
    # The trial's new Git root excludes ancestor project MCP definitions.
    return tuple(name for name in DISABLED_MCP if name in config.get("mcp_servers", {}))


def new_scratch():
    # Short paths avoid MAX_PATH failures in nested pytest fixtures on Windows.
    parent = ROOT / ".t"
    parent.mkdir(exist_ok=True)
    path = parent / ("rb-" + uuid4().hex[:8])
    path.mkdir(exist_ok=False)
    return path


def parse_junit(path):
    cases = list(ET.parse(path).getroot().iter("testcase")) if path.exists() else []
    failed = [case.find("failure") for case in cases if case.find("failure") is not None]
    return {
        "tests": len(cases),
        "failures": len(failed),
        "errors": sum(case.find("error") is not None for case in cases),
        "skipped": sum(case.find("skipped") is not None for case in cases),
        "assertion_failures": sum(
            "AssertionError" in ((item.text or "") + item.get("message", ""))
            or "assert " in ((item.text or "") + item.get("message", ""))
            for item in failed
        ),
    }


def grade(directory, trial, name, public_tests, hidden=False):
    receipt = directory / "receipts" / name
    receipt.mkdir(parents=True, exist_ok=False)
    if not hidden and not public_tests:
        result = {
            "passed": True,
            "not_applicable": True,
            "reason": "No public tests supplied",
            "tests": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "elapsed_seconds": 0.0,
            "exit_code": None,
        }
        write_json(receipt / "result.json", result)
        return result
    xml = receipt / "tests.xml"
    scratch = new_scratch() / "pytest"
    command = [
        sys.executable,
        "-B",
        "-X",
        "utf8",
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "-p",
        "no:cacheprovider",
        "-c",
        str(trial / "pytest.ini"),
        "-o",
        "addopts=",
        "--basetemp",
        str(scratch),
        "--junitxml",
        str(xml),
    ]
    if hidden:
        command += ["--noconftest", str(directory / "grade" / "test_hidden_resume.py")]
    else:
        command += ["--confcutdir", str(trial / "tests")]
        command += [str(trial / path) for path in public_tests]
    environment = test_environment(trial)
    probe = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import importlib.util; print(importlib.util.find_spec('jev_context').origin)",
        ],
        cwd=trial,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    expected = (trial / "src/jev_context/__init__.py").resolve()
    if probe.returncode or Path(probe.stdout.strip()).resolve() != expected:
        result = {
            "passed": False,
            "exit_code": None,
            "tests": 0,
            "failures": 0,
            "errors": 1,
            "skipped": 0,
            "elapsed_seconds": 0.0,
            "error": "Trial source import could not be verified",
            "probe_stdout": probe.stdout,
            "probe_stderr": probe.stderr,
        }
        write_json(receipt / "result.json", result)
        return result
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=trial,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
        exit_code, timed_out = None, True
    (receipt / "stdout.txt").write_text(stdout, encoding="utf-8")
    (receipt / "stderr.txt").write_text(stderr, encoding="utf-8")
    result = {
        "command": command,
        "scratch": str(scratch),
        "imported_package": str(expected),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "elapsed_seconds": time.monotonic() - started,
        **parse_junit(xml),
    }
    result["passed"] = (
        exit_code == 0
        and result["tests"] > 0
        and not any(result[key] for key in ("failures", "errors", "skipped"))
    )
    write_json(receipt / "result.json", result)
    return result


def task_prompt(case):
    allowed = ", ".join(case["allowed_files"])
    python = str(Path(sys.executable).resolve())
    return f"""{case["prompt"].strip()}

수정 허용 파일: {allowed}
context.md가 비어 있지 않으면 작업 재개를 위한 제공 자료로 읽어라.
공개 테스트는 읽고 실행할 수 있지만 테스트와 설정, TASK.md, context.md는 수정하지 마라.
네트워크, MCP, 외부자료, 다른 agent, 다른 대화를 사용하지 마라.
현재 작업 폴더에 제공된 코드와 자료만 읽어라. 상위 폴더, 원본 저장소, 다른 조건의
폴더, 채점 자료, 홈 디렉터리의 대화 기록을 읽지 마라. Git 설정/기록도 변경하지 마라.
외부 경로 예외는 Python 실행기 {python}, 이 실행기의 표준 라이브러리·설치 의존성,
환경변수 TEMP/TMP가 가리키는 이 실행 전용 임시 폴더뿐이다.
Python 실행은 위 절대 경로로 -B 옵션을 붙여라. PYTHONPATH는 현재 폴더의 src,
PYTHONDONTWRITEBYTECODE=1, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1이 이미 설정되어 있다.
pytest에는 -p no:cacheprovider -c pytest.ini -o addopts= 옵션을 붙이고,
임시 파일이 필요하면 환경변수 TEMP/TMP가 가리키는 폴더를 사용하라.
작업 폴더 안에 캐시나 임시 파일을 만들지 마라. 변경 내용과 실제 확인 결과를 설명하라.
"""


def prepare(directory, case_path, contexts, source_root=None):
    source_root = ROOT if source_root is None else source_root.resolve()
    case = read_json(case_path)
    for key in ("allowed_files", "public_tests"):
        if not isinstance(case.get(key), list) or any(not isinstance(v, str) for v in case[key]):
            raise ValueError(f"case.{key} must be a list of repository-relative paths")
        case[key] = [Path(value).as_posix() for value in case[key]]
    if (
        not case["allowed_files"]
        or not isinstance(case.get("prompt"), str)
        or not case["prompt"].strip()
    ):
        raise ValueError("Nonempty allowed_files and prompt are required")
    hidden = Path(case["hidden_test"])
    if not hidden.is_absolute() or not hidden.is_file():
        raise ValueError("hidden_test must name an existing absolute file")
    sources = sorted(
        path
        for path in (source_root / "src/jev_context").rglob("*")
        if path.is_file() and path.suffix in (".py", ".json") and "__pycache__" not in path.parts
    )
    sources += [source_root / "tests/conftest.py"]
    sources += [relative_file(value, source_root) for value in case["public_tests"]]
    sources = sorted(set(sources))
    hashes = {path.relative_to(source_root).as_posix(): sha(path) for path in sources}
    for value in case["allowed_files"]:
        relative_file(value, source_root)
        if value not in hashes or not value.startswith("src/jev_context/"):
            raise ValueError(f"Allowed file must be in the copied product snapshot: {value}")
    resources = case.get("resources", {})
    if not isinstance(resources, dict):
        raise ValueError("resources must map trial-relative sources/ paths to absolute files")
    resource_paths = {}
    for target, value in resources.items():
        relative = Path(target)
        source = Path(value)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or ":" in target
            or not relative.as_posix().startswith("sources/")
            or not source.is_absolute()
            or not source.is_file()
        ):
            raise ValueError(f"Invalid context resource mapping: {target}")
        resource_paths[relative.as_posix()] = source
    resource_hashes = {name: sha(path) for name, path in resource_paths.items()}
    raw = {key: (contexts / f"{key}.json").read_bytes() for key in ("e1", "e2", "generation")}
    parsed = {key: json.loads(value.decode("utf-8-sig")) for key, value in raw.items()}
    prompt = task_prompt(case)
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required to establish each isolated project root")
    directory.mkdir(parents=True, exist_ok=False)
    inputs = directory / "inputs"
    inputs.mkdir()
    shutil.copyfile(case_path, inputs / "case.json")
    for key, value in raw.items():
        (inputs / f"{key}.json").write_bytes(value)
    (directory / "grade").mkdir()
    shutil.copyfile(hidden, directory / "grade/test_hidden_resume.py")
    trials = {}
    for block, order in enumerate(ORDERS, 1):
        for position, condition in enumerate(order, 1):
            number = f"{len(trials) + 1:02}"
            trial = directory / "t" / number / "w"
            trial.mkdir(parents=True)
            for source in sources:
                target = trial / source.relative_to(source_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            for name, source in resource_paths.items():
                target = trial / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            (trial / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            (trial / "TASK.md").write_text(prompt, encoding="utf-8")
            (trial / "context.md").write_bytes(b"" if condition == "e0" else raw[condition])
            initialized = subprocess.run(
                [git, "init", "--quiet", str(trial)], capture_output=True, text=True, timeout=30
            )
            if initialized.returncode:
                raise RuntimeError(f"git init failed: {initialized.stderr}")
            files = snapshot(trial)
            if any(files.get(path) != digest for path, digest in hashes.items()):
                raise RuntimeError("Source changed during snapshot preparation")
            if any(files.get(path) != digest for path, digest in resource_hashes.items()):
                raise RuntimeError("Common resource changed during snapshot preparation")
            trials[number] = {
                "condition": condition,
                "block": block,
                "position": position,
                "path": f"t/{number}/w",
                "files": files,
                "judgments": context_status(parsed.get(condition, {})),
            }
    baseline_trial = directory / trials["01"]["path"]
    public = grade(directory, baseline_trial, "baseline-public", case["public_tests"])
    hidden_result = grade(directory, baseline_trial, "baseline-hidden", case["public_tests"], True)
    baseline = {"public": public, "hidden": hidden_result}
    write_json(directory / "baseline.json", baseline)
    if not public["passed"]:
        raise RuntimeError("Public baseline failed; benchmark not frozen (receipts preserved)")
    if not (
        hidden_result["exit_code"] == 1
        and hidden_result["failures"] > 0
        and hidden_result["assertion_failures"] == hidden_result["failures"]
        and hidden_result["errors"] == 0
        and hidden_result["skipped"] == 0
    ):
        raise RuntimeError("Hidden baseline must show assertion failures only; receipts preserved")
    if snapshot(baseline_trial) != trials["01"]["files"]:
        raise RuntimeError("Baseline modified trial inputs; benchmark not frozen")
    auto_trust = automatic_trust_paths([directory / item["path"] for item in trials.values()])
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "task": case.get("task_id", "existing_resume_defect"),
        "synthetic_defect": False,
        "trials_per_condition": 3,
        "execution_order": list(trials),
        "latin_orders": ORDERS,
        "promotion_eligible": False,
        "effect_established": False,
        "model_override": None,
        "reasoning_override": None,
        "disabled_mcp_servers": configured_disabled_mcp(),
        "disabled_features": DISABLED_FEATURES,
        "user_config": user_config_observation(auto_trust),
        "config_auto_trust_paths": auto_trust,
        "source_root": str(source_root),
        "allowed_files": case["allowed_files"],
        "public_tests": case["public_tests"],
        "source_hashes": hashes,
        "resource_hashes": resource_hashes,
        "resource_paths": {name: str(path) for name, path in resource_paths.items()},
        "input_hashes": snapshot(inputs),
        "hidden_test_sha256": sha(directory / "grade/test_hidden_resume.py"),
        "script_sha256": sha(Path(__file__)),
        "python": str(Path(sys.executable).resolve()),
        "prompt": prompt,
        "trials": trials,
        "baseline": baseline,
        "context_generation": parsed["generation"],
        "context_model_effect": "Raw returned E2 context retained, including abstention/shadow",
    }
    write_json(directory / "manifest.freeze.json", manifest)
    (directory / "manifest.freeze.sha256").write_text(
        sha(directory / "manifest.freeze.json"), encoding="ascii"
    )
    return {"prepared": str(directory), "trials": len(trials), "baseline": baseline}


def verify_freeze(directory):
    path = directory / "manifest.freeze.json"
    if sha(path) != (directory / "manifest.freeze.sha256").read_text(encoding="ascii"):
        raise RuntimeError("Frozen manifest changed")
    manifest = read_json(path)
    if sha(Path(__file__)) != manifest["script_sha256"]:
        raise RuntimeError("Benchmark runner changed after preparation")
    if sha(directory / "grade/test_hidden_resume.py") != manifest["hidden_test_sha256"]:
        raise RuntimeError("Hidden grading test changed")
    if snapshot(directory / "inputs") != manifest["input_hashes"]:
        raise RuntimeError("Frozen context or descriptor changed")
    if str(Path(sys.executable).resolve()) != manifest["python"]:
        raise RuntimeError("Benchmark Python executable changed")
    return manifest


def json_events(raw):
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            yield value


def observed_stdout(raw):
    events = list(json_events(raw))
    ids = {event.get("thread_id") for event in events if event.get("type") == "thread.started"}
    ids.discard(None)
    usage = [event.get("usage") for event in events if event.get("type") == "turn.completed"]
    return {
        "errors": [event for event in events if event.get("type") in {"error", "turn.failed"}],
        "thread_id": next(iter(ids)) if len(ids) == 1 else None,
        "completed_turns": len(usage),
        "stdout_turn_usage": usage,
        "stdout_tool_count": sum(
            event.get("type") == "item.completed"
            and event.get("item", {}).get("type")
            in ("command_execution", "mcp_tool_call", "web_search")
            for event in events
        ),
    }


def parse_rollout(path, thread_id, trial):
    events = list(json_events(path.read_text(encoding="utf-8", errors="replace")))
    metas = [event.get("payload", {}) for event in events if event.get("type") == "session_meta"]
    if len(metas) != 1 or metas[0].get("id") != thread_id:
        raise RuntimeError("Rollout session ID does not match this execution")
    if Path(metas[0].get("cwd", "")).resolve() != trial.resolve():
        raise RuntimeError("Rollout workspace does not match this trial")
    turns = [event.get("payload", {}) for event in events if event.get("type") == "turn_context"]
    models = sorted({turn["model"] for turn in turns if isinstance(turn.get("model"), str)})
    efforts = sorted(
        {
            turn.get("effort", turn.get("reasoning_effort"))
            for turn in turns
            if isinstance(turn.get("effort", turn.get("reasoning_effort")), str)
        }
    )
    usage = None
    for event in events:
        payload = event.get("payload", {})
        if (
            event.get("type") == "event_msg"
            and payload.get("type") == "token_count"
            and isinstance(payload.get("info"), dict)
        ):
            candidate = payload["info"].get("total_token_usage")
            if isinstance(candidate, dict):
                usage = candidate
    tools = [
        event.get("payload", {})
        for event in events
        if event.get("type") == "response_item"
        and event.get("payload", {}).get("type")
        in ("function_call", "custom_tool_call", "mcp_tool_call")
    ]
    complete = (
        len(models) == 1
        and len(efforts) == 1
        and isinstance(usage, dict)
        and all(
            isinstance(usage.get(key), int) and usage[key] >= 0
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
        )
        and usage["input_tokens"] >= usage["cached_input_tokens"]
    )
    return {
        "rollout_path": str(path),
        "rollout_sha256": sha(path),
        "observed_models": models,
        "observed_reasoning_efforts": efforts,
        "total_token_usage": usage,
        "uncached_input_tokens": (usage["input_tokens"] - usage["cached_input_tokens"])
        if complete
        else None,
        "tool_call_count": len(tools),
        "measurement_complete": complete,
    }


def observe_rollout(thread_id, trial, sessions=None):
    if not isinstance(thread_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", thread_id):
        return {"measurement_complete": False, "rollout_error": "No unique thread.started ID"}
    root = sessions or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    # Only the exact returned ID is matched. Never read other sessions for inference.
    paths = list(root.rglob(f"*-{thread_id}.jsonl"))
    if len(paths) != 1:
        return {
            "measurement_complete": False,
            "rollout_error": f"Expected one exact matching rollout, found {len(paths)}",
        }
    try:
        return parse_rollout(paths[0], thread_id, trial)
    except (OSError, ValueError, RuntimeError) as exc:
        return {"measurement_complete": False, "rollout_error": str(exc)}


def stop_owned_tree(process):
    if process.poll() is not None:
        return None
    if os.name == "nt":
        stopped = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15
        )
        if process.poll() is None:
            process.kill()
        return stopped.returncode
    os.killpg(process.pid, signal.SIGKILL)
    return 0


def run_codex(directory, trial, number, executable, prompt, timeout, auto_trust_paths=()):
    receipt = directory / "runs" / number
    receipt.mkdir(parents=True, exist_ok=False)
    scratch = new_scratch()
    command = [executable, "exec", "--json", "--sandbox", "workspace-write", "--cd", str(trial)]
    for name in DISABLED_FEATURES:
        command += ["--disable", name]
    for name in configured_disabled_mcp():
        command += ["-c", f"mcp_servers.{name}.enabled=false"]
    command += ["--output-last-message", str(receipt / "answer.md"), "-"]
    environment = {
        **test_environment(trial),
        "TEMP": str(scratch),
        "TMP": str(scratch),
        "TMPDIR": str(scratch),
    }
    started = time.monotonic()
    result = {
        "command": command,
        "exit_code": None,
        "timed_out": False,
        "started_at": datetime.now(UTC).isoformat(),
        "scratch": str(scratch),
        "user_config": user_config_observation(auto_trust_paths),
    }
    with (receipt / "stdout.jsonl").open("wb") as out, (receipt / "stderr.txt").open("wb") as err:
        process = None
        try:
            process = subprocess.Popen(
                command,
                cwd=trial,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                start_new_session=os.name != "nt",
            )
            result["pid"] = process.pid
            try:
                process.communicate(prompt.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                result["tree_stop_exit_code"] = stop_owned_tree(process)
                process.communicate(timeout=15)
            result["exit_code"] = process.returncode
        except (OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            if process is not None:
                result["tree_stop_exit_code"] = stop_owned_tree(process)
            result["error"] = repr(exc)
    result["elapsed_seconds"] = time.monotonic() - started
    result.update(
        observed_stdout((receipt / "stdout.jsonl").read_text(encoding="utf-8", errors="replace"))
    )
    result.update(observe_rollout(result["thread_id"], trial))
    result["answer_present"] = (receipt / "answer.md").exists()
    write_json(receipt / "execution.json", result)
    return result


def execute(directory, number, timeout=480):
    manifest = verify_freeze(directory)
    if number not in manifest["trials"]:
        raise ValueError(f"Unknown trial: {number}")
    # Each call runs one new session. Exclusive creation also blocks concurrent executions.
    lock = directory / "execution.lock"
    with lock.open("x", encoding="ascii") as handle:
        handle.write(str(os.getpid()))
    try:
        for preceding in manifest["execution_order"]:
            if preceding == number:
                break
            if not (directory / "runs" / preceding / "result.json").is_file():
                raise RuntimeError(f"Latin order requires trial {preceding} before {number}")
            previous_result = read_json(directory / "runs" / preceding / "result.json")
            if execution_status(previous_result["execution"]) == "usage_limited":
                raise RuntimeError(
                    "Usage limit interrupted this batch; preserve it and start a new batch"
                )
        for key, item in manifest["trials"].items():
            previous = directory / "runs" / key / "result.json"
            expected = read_json(previous)["final_hashes"] if previous.exists() else item["files"]
            if snapshot(directory / item["path"]) != expected:
                raise RuntimeError(f"Frozen or completed trial changed: {key}")
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError("codex executable is unavailable")
        if (
            user_config_observation(manifest["config_auto_trust_paths"])["effective_sha256"]
            != manifest["user_config"]["effective_sha256"]
        ):
            raise RuntimeError("User configuration changed after the benchmark was frozen")
        version = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=15
        )
        item = manifest["trials"][number]
        trial = directory / item["path"]
        execution = run_codex(
            directory,
            trial,
            number,
            executable,
            manifest["prompt"],
            timeout,
            manifest["config_auto_trust_paths"],
        )
        execution["cli_version"] = version.stdout.strip() or None
        verify_freeze(directory)
        current = snapshot(trial)
        modified = changes(item["files"], current)
        violations = [path for path in modified if path not in manifest["allowed_files"]]
        result = {
            "trial": number,
            "condition": item["condition"],
            "block": item["block"],
            "position": item["position"],
            "judgments": item["judgments"],
            "execution": execution,
            "changed_files": modified,
            "final_hashes": current,
            "scope_violations": violations,
            "public": None,
            "hidden": None,
        }
        if not violations:
            result["public"] = grade(directory, trial, number + "-public", manifest["public_tests"])
            result["hidden"] = grade(
                directory, trial, number + "-hidden", manifest["public_tests"], hidden=True
            )
            if snapshot(trial) != current:
                result["scope_violations"].append("grading_modified_trial")
        result["passed"] = (
            not result["scope_violations"]
            and execution["exit_code"] == 0
            and not execution["timed_out"]
            and execution["completed_turns"] > 0
            and bool(result["public"] and result["public"]["passed"])
            and bool(result["hidden"] and result["hidden"]["passed"])
        )
        result["grading_seconds"] = sum(
            (result[key] or {}).get("elapsed_seconds", 0.0) for key in ("public", "hidden")
        )
        result["code_plus_grading_seconds"] = (
            execution["elapsed_seconds"] + result["grading_seconds"]
        )
        write_json(directory / "runs" / number / "result.json", result)
        report(directory)
        return result
    finally:
        lock.unlink()


def median(values):
    return statistics.median(values) if values else None


def execution_status(execution):
    errors = json.dumps(execution.get("errors", []), ensure_ascii=False).lower()
    if "usage limit" in errors or "rate_limit_exceeded" in errors:
        return "usage_limited"
    if execution.get("timed_out"):
        return "timed_out"
    if execution.get("exit_code") != 0 or execution.get("completed_turns", 0) < 1:
        return "execution_failed"
    if not execution.get("answer_present"):
        return "missing_answer"
    return "completed"


def report(directory):
    manifest = verify_freeze(directory)
    trials = {}
    for number, item in manifest["trials"].items():
        path = directory / "runs" / number / "result.json"
        if path.exists():
            result = read_json(path)
            if snapshot(directory / item["path"]) != result["final_hashes"]:
                result["scope_violations"].append("completed_trial_changed_later")
                result["passed"] = False
            trials[number] = result
    value = summarize_trials(manifest, trials)
    write_json(directory / "results.json", value)
    return value


def summarize_trials(manifest, trials):
    """Summarize attempts without mistaking infrastructure failures for task performance.

    Normally completed repairs that fail grading remain in the timing denominator.
    Raw attempts, including incomplete token observations, remain in trials.
    """
    conditions = {}
    for condition in ("e0", "e1", "e2"):
        runs = [result for result in trials.values() if result["condition"] == condition]
        completed = [r for r in runs if execution_status(r["execution"]) == "completed"]
        eligible = [r for r in completed if not r["scope_violations"]]
        measured = [r for r in eligible if r["execution"]["measurement_complete"]]
        generation = manifest["context_generation"]
        generation_entry = generation.get(condition, {}) if isinstance(generation, dict) else {}
        generation_seconds = 0.0 if condition == "e0" else generation_entry.get("elapsed_seconds")
        replay_seconds = median([r["code_plus_grading_seconds"] for r in eligible])
        conditions[condition] = {
            "attempted_trials": len(runs),
            "completed_trials": len(completed),
            "performance_trials": len(eligible),
            "execution_status_counts": {
                status: sum(execution_status(r["execution"]) == status for r in runs)
                for status in sorted({execution_status(r["execution"]) for r in runs})
            },
            "scope_violation_trials": sum(bool(r["scope_violations"]) for r in runs),
            "passed_trials": sum(run["passed"] for run in runs),
            "measurement_complete_trials": len(measured),
            "median_attempt_seconds": median([r["execution"]["elapsed_seconds"] for r in runs]),
            "median_code_seconds": median([r["execution"]["elapsed_seconds"] for r in eligible]),
            "median_grading_seconds": median([r["grading_seconds"] for r in eligible]),
            "median_code_plus_grading_seconds": replay_seconds,
            "context_generation_seconds_once": generation_seconds,
            "median_context_replay_code_plus_grading_seconds": replay_seconds,
            "estimated_context_call_plus_replay_seconds": replay_seconds + generation_seconds
            if isinstance(generation_seconds, (int, float)) and replay_seconds is not None
            else None,
            "median_total_tokens": median(
                [r["execution"]["total_token_usage"]["total_tokens"] for r in measured]
            ),
            "median_uncached_input_tokens": median(
                [r["execution"]["uncached_input_tokens"] for r in measured]
            ),
            "median_cached_input_tokens": median(
                [r["execution"]["total_token_usage"]["cached_input_tokens"] for r in measured]
            ),
            "median_output_tokens": median(
                [r["execution"]["total_token_usage"]["output_tokens"] for r in measured]
            ),
            "median_tool_calls": median([r["execution"]["tool_call_count"] for r in measured]),
        }
    value = {
        "updated_at": datetime.now(UTC).isoformat(),
        "task": manifest["task"],
        "scope": "One existing defect, three independent sessions per condition, Latin orders",
        "promotion_eligible": False,
        "effect_established": False,
        "limitations": [
            "One task does not establish general benefit or statistical significance",
            "Context generation is measured separately and reused across three trials",
            "Context-call plus replay estimate excludes model preparation; full setup is reported separately",
            "Performance medians exclude incomplete executions and scope violations, but retain graded failures",
            "Read isolation relies on instructions plus project roots, not a sealed filesystem",
        ],
        "planned_trials": 9,
        "attempted_trials": len(trials),
        "completed_trials": sum(c["completed_trials"] for c in conditions.values()),
        "comparison_complete": all(
            c["performance_trials"] == 3 and c["measurement_complete_trials"] == 3
            for c in conditions.values()
        ),
        "conditions": conditions,
        "context_generation": manifest["context_generation"],
        "trials": trials,
    }
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for name in ("prepare", "execute", "report"):
        sub = modes.add_parser(name)
        sub.add_argument("--directory", required=True, type=Path)
        if name == "prepare":
            sub.add_argument("--case", required=True, type=Path)
            sub.add_argument("--contexts", required=True, type=Path)
            sub.add_argument("--source-root", type=Path)
        if name == "execute":
            sub.add_argument("--trial", required=True)
            sub.add_argument("--timeout", type=int, default=480)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.mode == "prepare":
        result = prepare(directory, args.case.resolve(), args.contexts.resolve(), args.source_root)
    elif args.mode == "execute":
        if not 1 <= args.timeout <= 900:
            parser.error("--timeout must be between 1 and 900 seconds")
        result = execute(directory, args.trial.zfill(2), args.timeout)
    else:
        result = report(directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
