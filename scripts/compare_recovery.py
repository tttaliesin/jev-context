"""Freeze and run one real hook repair task under three independent CLI conditions."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORDER = ("e1", "e0", "e2")
ALLOWED = "src/jev_context/session_hooks.py"
PROMPT = """SessionStart/UserPromptSubmit가 현재 프로젝트 root와 일치하지 않는 저장소에서
복원/갱신 안내를 내보내는 결함을 고쳐라. 기존 project_id 검증과 정상 복원을 유지하고,
저장소 읽기 전용·기존 데이터 무변경을 보장하라.
수정 허용 파일은 src/jev_context/session_hooks.py 하나뿐이다.
네트워크, MCP, 외부자료, 다른 agent를 사용하지 마라.
이 작업 폴더에 제공된 코드와 context.md만 읽어라. 상위 폴더, 원본 저장소,
다른 조건의 폴더, 채점 파일을 읽지 마라. 주어진 공개 테스트는 읽어도 된다.
캐시나 임시 파일을 추가하지 말고 변경한 내용과 확인한 결과를 설명하라.
"""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): sha(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def changes(before, after):
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


def context_status(value):
    judgments = []
    if isinstance(value, dict):
        if isinstance(value.get("judgment"), dict):
            judgments.append(
                {key: value["judgment"].get(key) for key in ("status", "mode", "reason")}
            )
        for item in value.values():
            judgments.extend(context_status(item))
    elif isinstance(value, list):
        for item in value:
            judgments.extend(context_status(item))
    return judgments


def test_environment(trial):
    return {
        **os.environ,
        "PYTHONPATH": str(trial / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ADDOPTS": "",
    }


def grade(directory, trial, name, hidden):
    receipt = directory / "receipts" / name
    receipt.mkdir(parents=True, exist_ok=False)
    target = (
        directory / "grade/test_hook_project_binding.py"
        if hidden
        else trial / "tests/test_session_hooks.py"
    )
    xml = receipt / "tests.xml"
    scratch = ROOT / ".t" / (directory.name + "-" + name)
    scratch.parent.mkdir(exist_ok=True)
    if scratch.exists():
        raise RuntimeError(f"Refusing to reuse prior grading scratch directory: {scratch}")
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
    command += ["--noconftest"] if hidden else ["--confcutdir", str(trial / "tests")]
    command.append(str(target))
    environment = test_environment(trial)
    probe = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import importlib.util; "
            "print(importlib.util.find_spec('jev_context.session_hooks').origin)",
        ],
        cwd=trial,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    expected_import = (trial / ALLOWED).resolve()
    if probe.returncode or Path(probe.stdout.strip()).resolve() != expected_import:
        result = {
            "passed": False,
            "exit_code": None,
            "tests": 0,
            "failures": 0,
            "errors": 1,
            "skipped": 0,
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
            timeout=60,
        )
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
        exit_code, timed_out = None, True
    (receipt / "stdout.txt").write_text(stdout, encoding="utf-8")
    (receipt / "stderr.txt").write_text(stderr, encoding="utf-8")
    cases = list(ET.parse(xml).getroot().iter("testcase")) if xml.exists() else []
    result = {
        "command": command,
        "imported_hook": str(expected_import),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "elapsed_seconds": time.monotonic() - started,
        "tests": len(cases),
        "failures": sum(case.find("failure") is not None for case in cases),
        "errors": sum(case.find("error") is not None for case in cases),
        "skipped": sum(case.find("skipped") is not None for case in cases),
    }
    result["passed"] = (
        exit_code == 0
        and bool(cases)
        and not any(result[key] for key in ("failures", "errors", "skipped"))
    )
    write_json(receipt / "result.json", result)
    return result


def prepare(directory, contexts):
    context_inputs = {"e1": contexts / "off.json", "e2": contexts / "model.json"}
    # Validate everything before creating the destination. Context bytes are preserved verbatim.
    context_bytes = {key: path.read_bytes() for key, path in context_inputs.items()}
    statuses = {
        key: context_status(json.loads(raw.decode("utf-8-sig")))
        for key, raw in context_bytes.items()
    }
    sources = sorted((ROOT / "src/jev_context").glob("*.py"))
    sources += sorted((ROOT / "src/jev_context").glob("*.json"))
    sources += [ROOT / "tests/conftest.py", ROOT / "tests/test_session_hooks.py"]
    hidden = ROOT / "tests/test_hook_project_binding.py"
    original_hashes = {path.relative_to(ROOT).as_posix(): sha(path) for path in sources}
    hidden_hash = sha(hidden)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "grade").mkdir()
    shutil.copyfile(hidden, directory / "grade" / hidden.name)
    trials = {}
    for condition in ("e0", "e1", "e2"):
        trial = directory / condition
        trial.mkdir()
        for source in sources:
            target = trial / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (trial / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (trial / "TASK.md").write_text(PROMPT, encoding="utf-8")
        if condition == "e0":
            (trial / "context.md").write_bytes(b"")
        else:
            (trial / "context.md").write_bytes(context_bytes[condition])
        files = snapshot(trial)
        if any(files.get(path) != value for path, value in original_hashes.items()):
            raise RuntimeError("Source changed during snapshot preparation")
        trials[condition] = {
            "files": files,
            "context": "none" if condition == "e0" else context_inputs[condition].name,
            "judgments": statuses.get(condition, []),
        }
    if sha(directory / "grade" / hidden.name) != hidden_hash:
        raise RuntimeError("Hidden test changed during snapshot preparation")
    baseline_public = grade(directory, directory / "e0", "bp", hidden=False)
    baseline_hidden = grade(directory, directory / "e0", "bh", hidden=True)
    if not baseline_public["passed"]:
        raise RuntimeError("Public baseline failed; benchmark not frozen")
    if not (
        baseline_hidden["exit_code"] == 1
        and baseline_hidden["failures"] > 0
        and baseline_hidden["errors"] == 0
        and baseline_hidden["skipped"] == 0
    ):
        raise RuntimeError("Hidden baseline must demonstrate a behavior failure")
    if snapshot(directory / "e0") != trials["e0"]["files"]:
        raise RuntimeError("Baseline modified the trial inputs")
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "task": "existing_hook_project_root_binding_defect",
        "synthetic_defect": False,
        "trials_per_condition": 1,
        "execution_order": list(ORDER),
        "promotion_eligible": False,
        "human_reviewed": False,
        "model_override": None,
        "reasoning_override": None,
        "context_model_effect": "E2 keeps the returned state, including abstention or shadow",
        "allowed_file": ALLOWED,
        "source_hashes": original_hashes,
        "hidden_test_sha256": hidden_hash,
        "script_sha256": sha(Path(__file__)),
        "prompt": PROMPT,
        "trials": trials,
        "baseline": {"public": baseline_public, "hidden": baseline_hidden},
    }
    manifest_path = directory / "manifest.freeze.json"
    write_json(manifest_path, manifest)
    (directory / "manifest.freeze.sha256").write_text(sha(manifest_path), encoding="ascii")
    return {"prepared": str(directory), "baseline": manifest["baseline"]}


def verify_freeze(directory):
    manifest_path = directory / "manifest.freeze.json"
    if sha(manifest_path) != (directory / "manifest.freeze.sha256").read_text(encoding="ascii"):
        raise RuntimeError("Frozen manifest changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sha(directory / "grade/test_hook_project_binding.py") != manifest["hidden_test_sha256"]:
        raise RuntimeError("Hidden grading test changed")
    if sha(Path(__file__)) != manifest["script_sha256"]:
        raise RuntimeError("Comparison runner changed after preparation")
    return manifest


def observed_usage(stdout, stderr):
    usage, model, turns = None, None, 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            turns += 1
            usage = event.get("usage")
        if isinstance(event.get("model"), str):
            model = event["model"]
    for line in stderr.splitlines():
        if line.strip().startswith("model:"):
            model = line.split(":", 1)[1].strip()
    return {"last_turn_usage": usage, "completed_turns": turns, "observed_model": model}


def run_codex(directory, trial, condition, executable, prompt):
    receipt = directory / "runs" / condition
    receipt.mkdir(parents=True, exist_ok=False)
    command = [
        executable,
        "exec",
        "--ephemeral",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(trial),
        "--output-last-message",
        str(receipt / "answer.md"),
        "-",
    ]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(trial / "src")}
    started = time.monotonic()
    result = {"command": command, "exit_code": None, "timed_out": False}
    with (receipt / "stdout.jsonl").open("wb") as out, (receipt / "stderr.txt").open("wb") as err:
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
            try:
                process.communicate(prompt.encode("utf-8"), timeout=300)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                if os.name == "nt":
                    stopped = subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=15,
                    )
                    result["tree_stop_exit_code"] = stopped.returncode
                    if process.poll() is None:
                        process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=15)
            result["exit_code"] = process.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            result["error"] = repr(exc)
    result["elapsed_seconds"] = time.monotonic() - started
    result.update(
        observed_usage(
            (receipt / "stdout.jsonl").read_text(encoding="utf-8", errors="replace"),
            (receipt / "stderr.txt").read_text(encoding="utf-8", errors="replace"),
        )
    )
    result["answer_present"] = (receipt / "answer.md").exists()
    write_json(receipt / "execution.json", result)
    return result


def execute(directory):
    manifest = verify_freeze(directory)
    if (directory / "runs").exists() or (directory / "results.json").exists():
        raise RuntimeError("Execution already started; prepare a new comparison to repeat it")
    for condition, trial in manifest["trials"].items():
        if snapshot(directory / condition) != trial["files"]:
            raise RuntimeError(f"Frozen input changed before execution: {condition}")
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("codex executable is unavailable")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=15)
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "conditions": {},
        "cli_version": version.stdout.strip() or None,
        "model_configuration": "user CLI defaults; no model or reasoning override",
        "scope": "one existing defect; one run per condition; fixed e1,e0,e2 order",
        "promotion_eligible": False,
        "effect_established": False,
    }
    write_json(directory / "results.json", report)
    for condition in ORDER:
        verify_freeze(directory)
        trial = directory / condition
        original = manifest["trials"][condition]["files"]
        if snapshot(trial) != original:
            raise RuntimeError(f"Unrun condition was modified: {condition}")
        execution = run_codex(directory, trial, condition, executable, manifest["prompt"])
        verify_freeze(directory)
        current = snapshot(trial)
        modified = changes(original, current)
        scope_violations = [path for path in modified if path != ALLOWED]
        result = {
            "context": manifest["trials"][condition]["context"],
            "judgments": manifest["trials"][condition]["judgments"],
            "execution": execution,
            "changed_files": modified,
            "final_hashes": current,
            "scope_violations": scope_violations,
            "public": None,
            "hidden": None,
        }
        if not scope_violations:
            result["public"] = grade(directory, trial, condition + "p", hidden=False)
            result["hidden"] = grade(directory, trial, condition + "h", hidden=True)
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
        report["conditions"][condition] = result
        write_json(directory / "results.json", report)
    verify_freeze(directory)
    for condition, result in report["conditions"].items():
        if snapshot(directory / condition) != result["final_hashes"]:
            result["scope_violations"].append("completed_condition_changed_later")
            result["passed"] = False
    report["finished_at"] = datetime.now(UTC).isoformat()
    write_json(directory / "results.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--contexts", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.prepare and args.contexts is None:
        parser.error("--prepare requires --contexts")
    directory = args.directory.resolve()
    result = prepare(directory, args.contexts.resolve()) if args.prepare else execute(directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
