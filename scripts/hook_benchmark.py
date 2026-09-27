"""PostToolUse hook on/off comparison: frozen seeded repairs run with real `codex exec`.

prepare  build reference/seeded workspaces, prove reference passes and seed fails, freeze trials
run      print (default) or execute one trial with codex exec; never runs without --execute
grade    fixed checks plus the rule that only src/ may change
report   tokens, requests, tool calls, time and hook actions per condition from real logs
"""

import argparse
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from jev_context.common import digest, now

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/Scripts/python.exe"
HOOK = ROOT / "experiments/output_filter/tool_hooks.py"
DEFAULT_CODEX = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
HOOK_ARGS = ["--min-bytes", "24000", "--budget-bytes", "8000"]  # the deployed filter settings
CONDITIONS = ("observe", "filter")
REPEATS = 3
TASKS = {
    "tests-uid": {
        "file": "common.py",
        "before": 'return f"{prefix}-{uuid.uuid4().hex}"',
        "after": 'return f"{prefix}_{uuid.uuid4().hex}"',
        "statement": (
            "The test suite of this project started failing after a recent change. "
            "Run the tests, find the root cause in the source code under src/, and fix it "
            "so that the whole test suite passes."
        ),
        "hidden_check": None,
    },
    "log-budget": {
        "file": "budget.py",
        "before": 'if item.get("role") != "required_evidence" and not item.get("mandatory")',
        "after": 'if not item.get("mandatory")',
        "statement": (
            "Users report that context packets sometimes arrive with outcome ok but without "
            "evidence they marked as required. logs/jev-context.log is the production log from "
            "the incident window. Use it to find the root cause in the source code under src/ "
            "and fix it; required evidence must never be dropped silently."
        ),
        "hidden_check": "evaluations/coding/check_budget.py",
    },
}
IGNORED = {"__pycache__", ".t", ".pytest_cache", ".git"}
SAVED_OUTPUT = re.compile(r"hook_outputs|outputs[\\/]+[^\s'\"]*[0-9a-f]{32}\.txt", re.IGNORECASE)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def files(path):
    return {
        p.relative_to(path).as_posix(): digest(p.read_bytes())
        for p in sorted(path.rglob("*"))
        if p.is_file() and not IGNORED & set(p.relative_to(path).parts)
    }


def incident_log(seed=20260924, lines=4000):
    """Deterministic log: hundreds of unrelated ERRORs around a few budget warnings."""
    rng = random.Random(seed)
    hexid = lambda: f"{rng.getrandbits(32):08x}"  # noqa: E731
    noise = [
        lambda: (
            f"ERROR modal_engine  judgment abstained reason=deadline_exceeded "
            f"evaluation=eval-{hexid()} elapsed_ms={rng.randint(2001, 2900)}"
        ),
        lambda: (
            f"ERROR sources       source_changed during read source=source-{hexid()} "
            f"revision=rev-{hexid()}"
        ),
        lambda: (
            f"ERROR storage       database is locked; retry={rng.randint(1, 3)} op=work_record "
            f"work=work-{hexid()}"
        ),
        lambda: f"ERROR server        client disconnected before response request=req-{hexid()}",
    ]
    rows = []
    for index in range(lines):
        stamp = f"2026-09-21T10:{index // 60 % 60:02d}:{index % 60:02d}.{rng.randint(0, 999):03d}Z"
        roll = rng.random()
        if index % 331 == 17:
            body = (
                f"WARN  budget        bounded() removed evidence role=required_evidence "
                f"source=source-{hexid()} limit_bytes=4096 used_bytes={rng.randint(3000, 4096)} "
                "outcome=ok"
            )
        elif index % 661 == 40:
            body = (
                f"ERROR client        packet missing required evidence packet=packet-{hexid()} "
                f"required_ref=source-{hexid()} outcome=ok"
            )
        elif roll < 0.12:
            body = rng.choice(noise)()
        elif roll < 0.2:
            body = (
                f"WARN  sources       slow read source=source-{hexid()} ms={rng.randint(200, 900)}"
            )
        else:
            body = (
                f"INFO  server        request tool={rng.choice(['context_prepare', 'work_open', 'source_read', 'work_record'])} "
                f"work=work-{hexid()} request=req-{hexid()} elapsed_ms={rng.randint(3, 180)}"
            )
        rows.append(f"{stamp} {body}")
    return "\n".join(rows) + "\n"


def suite_tests():
    for test in sorted((ROOT / "tests").glob("*.py")):
        text = test.read_text(encoding="utf-8")
        if "from scripts" not in text and "import scripts" not in text:
            yield test


def build_reference(workspace):
    shutil.copytree(ROOT / "src", workspace / "src", ignore=shutil.ignore_patterns("__pycache__"))
    (workspace / "tests").mkdir()
    for test in suite_tests():
        shutil.copy2(test, workspace / "tests" / test.name)
    (workspace / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\npythonpath = src\n"
        "addopts = -p no:cacheprovider --basetemp=.t\n",
        encoding="utf-8",
    )
    (workspace / ".gitignore").write_text("__pycache__/\n.t/\n.pytest_cache/\n", encoding="utf-8")


def run_suite(workspace, receipt, basetemp=None):
    """The copied project suite, run the way a user would, against this workspace's src.

    Grading passes a fresh basetemp outside the workspace: a `.t` left by an agent's pytest run
    inside the Codex sandbox carries sandbox-only ACLs that the grader cannot clear.
    """
    xml = receipt.with_suffix(".xml")
    command = [str(PYTHON), "-X", "utf8", "-m", "pytest", "-q", "--junitxml", str(xml)]
    if basetemp:
        command += ["-o", "addopts=-p no:cacheprovider", f"--basetemp={basetemp}"]
    tick = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        env={**os.environ, "PYTHONPATH": str(workspace / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    cases = list(ET.parse(xml).getroot().iter("testcase")) if xml.exists() else []
    result = {
        "command": command,
        "exit_code": completed.returncode,
        "elapsed_ms": (time.monotonic() - tick) * 1000,
        "tests": len(cases),
        "failures": sum(c.find("failure") is not None for c in cases),
        "errors": sum(c.find("error") is not None for c in cases),
        "output_bytes": len((completed.stdout + completed.stderr).encode()),
        "passed": completed.returncode == 0 and bool(cases),
    }
    write_json(receipt, result)
    return result


def run_hidden(workspace, task, receipt):
    if not task["hidden_check"]:
        return None
    from scripts.coding_benchmark import run_checks

    return run_checks(workspace, ROOT / task["hidden_check"], receipt)


def hooks_config(trial, mode):
    state = trial / "h"
    command = [str(PYTHON), str(HOOK), "--state-dir", str(state)]
    command += ["--mode", mode, *HOOK_ARGS]
    if any(" " in part for part in command):
        raise ValueError("Hook command paths must not contain spaces")  # PowerShell quoting
    handler = {"type": "command", "command": " ".join(command), "timeout": 10}
    return {"hooks": {e: [{"hooks": [handler]}] for e in ("UserPromptSubmit", "PostToolUse")}}


def prompt(task):
    return (
        f"{task['statement']}\n\n"
        f"Work only inside the current directory. Python with the project dependencies: {PYTHON} "
        f"(for example `{PYTHON} -m pytest -q`). Do not modify files under tests/ or pytest.ini. "
        "Do not read files outside the current directory, except full tool outputs that a "
        "jev-context hook message points you to. Do not use MCP tools or other agents. "
        "When done, reply with the changed file paths and the root cause."
    )


def git(workspace, *args):
    subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)


def prepare(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    suite = {
        "created_at": now(),
        "provenance": "agent_authored_seeded_repairs",
        "human_reviewed": False,
        "hook_sha256": digest(HOOK.read_bytes()),
        "hook_args": HOOK_ARGS,
        "tasks": {},
        "trials": [],
    }
    for name, task in TASKS.items():
        reference = destination / "r" / name
        build_reference(reference)
        if name == "log-budget":
            (reference / "logs").mkdir()
            (reference / "logs/jev-context.log").write_text(incident_log(), encoding="utf-8")
        receipts = destination / "receipts"
        receipts.mkdir(exist_ok=True)
        base = run_suite(reference, receipts / f"{name}-reference-suite.json")
        hidden = run_hidden(reference, task, receipts / f"{name}-reference-hidden.json")
        if not base["passed"] or (hidden and not hidden["passed"]):
            raise RuntimeError(f"Reference fails: {name}")
        target = reference / "src/jev_context" / task["file"]
        original = target.read_text(encoding="utf-8")
        if original.count(task["before"]) != 1:
            raise ValueError(f"Seed location changed: {name}")
        seeded = destination / "s" / name
        shutil.copytree(reference, seeded, ignore=shutil.ignore_patterns(*IGNORED))
        (seeded / "src/jev_context" / task["file"]).write_text(
            original.replace(task["before"], task["after"], 1), encoding="utf-8"
        )
        broken = run_suite(seeded, receipts / f"{name}-seed-suite.json")
        broken_hidden = run_hidden(seeded, task, receipts / f"{name}-seed-hidden.json")
        if broken["passed"] and not (broken_hidden and not broken_hidden["passed"]):
            raise RuntimeError(f"Seed does not fail: {name}")
        suite["tasks"][name] = {
            "prompt": prompt(task),
            "reference_suite": base,
            "seed_suite": broken,
            "seed_hidden_passed": broken_hidden and broken_hidden["passed"],
            "hidden_check_sha256": digest((ROOT / task["hidden_check"]).read_bytes())
            if task["hidden_check"]
            else None,
        }
    order = 0
    for repeat in range(1, REPEATS + 1):
        for name in TASKS:
            # Alternate which condition runs first so order effects do not favor one side.
            conditions = CONDITIONS if repeat % 2 else tuple(reversed(CONDITIONS))
            for condition in conditions:
                trial_id = f"{name}-{condition}-{repeat}"
                order += 1
                trial = destination / "t" / f"{order:02d}"
                work = trial / "w"
                shutil.copytree(destination / "s" / name, work)
                (work / ".codex").mkdir()
                write_json(work / ".codex/hooks.json", hooks_config(trial, condition))
                git(work, "init", "-q")
                git(work, "add", "-A")
                git(work, "-c", "user.name=bench", "-c", "user.email=bench@local",
                    "commit", "-q", "-m", "seeded")  # fmt: skip
                suite["trials"].append({
                    "id": trial_id, "task": name, "condition": condition, "repeat": repeat,
                    "order": order, "directory": str(trial), "initial_files": files(work),
                })  # fmt: skip
    raw = (json.dumps(suite, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (destination / "suite.json").write_bytes(raw)
    (destination / "suite.sha256").write_text(digest(raw), encoding="utf-8")
    return suite


def load(destination):
    destination = Path(destination).resolve(strict=True)
    raw = (destination / "suite.json").read_bytes()
    if (destination / "suite.sha256").read_text(encoding="utf-8") != digest(raw):
        raise RuntimeError("suite.json changed after freezing")
    return destination, json.loads(raw)


def codex_binary(explicit=None):
    if explicit:
        return Path(explicit)
    found = sorted(DEFAULT_CODEX.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise FileNotFoundError("codex.exe not found; pass --codex")
    return found[-1]


def run_command(codex, trial, model):
    work = Path(trial["directory"]) / "w"
    return [
        str(codex), "exec", "--json", "--cd", str(work), "-m", model,
        "-s", "workspace-write", "--dangerously-bypass-hook-trust",
        # jev_context is only configured in this repository's .codex/config.toml, which trial
        # workspaces do not load; overriding it here would create an entry with no transport.
        "-c", "mcp_servers.web_image_bridge.enabled=false",
        "-o", str(Path(trial["directory"]) / "last-message.md"), "-",
    ]  # fmt: skip


def run(destination, trial_id, model, codex=None, execute=False):
    destination, suite = load(destination)
    trial = next(t for t in suite["trials"] if t["id"] == trial_id)
    directory = Path(trial["directory"])
    command = run_command(codex_binary(codex), trial, model)
    if not execute:
        return {"trial": trial_id, "command": command, "executed": False}
    if (directory / "run.json").exists():
        raise RuntimeError("Trial already ran; results are never overwritten")
    tick = time.monotonic()
    with open(directory / "events.jsonl", "wb") as out, open(directory / "stderr.log", "wb") as err:
        completed = subprocess.run(
            command,
            input=suite["tasks"][trial["task"]]["prompt"].encode("utf-8"),
            stdout=out,
            stderr=err,
            timeout=1800,
        )
    record = {
        "trial": trial_id,
        "model": model,
        "command": command,
        "exit_code": completed.returncode,
        "wall_seconds": time.monotonic() - tick,
        "finished_at": now(),
    }
    write_json(directory / "run.json", record)
    return record


def grade(destination, trial_id):
    destination, suite = load(destination)
    trial = next(t for t in suite["trials"] if t["id"] == trial_id)
    work = Path(trial["directory"]) / "w"
    current = files(work)
    changed = sorted(
        path
        for path in trial["initial_files"].keys() | current.keys()
        if trial["initial_files"].get(path) != current.get(path)
    )
    violations = [path for path in changed if not path.startswith("src/")]
    task = TASKS[trial["task"]]
    receipts = Path(trial["directory"]) / "receipts"
    receipts.mkdir(exist_ok=True)
    result = {
        "trial": trial_id,
        "changed": changed,
        "violations": violations,
        "suite": run_suite(work, receipts / "suite.json", Path(trial["directory"]) / "g"),
        "hidden": run_hidden(work, task, receipts / "hidden.json"),
    }
    hidden_ok = result["hidden"] is None or result["hidden"]["passed"]
    result["passed"] = not violations and result["suite"]["passed"] and hidden_ok
    write_json(Path(trial["directory"]) / "grade.json", result)
    return result


def session_metrics(events_path):
    thread = None
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        thread = thread or event.get("thread_id") or (event.get("thread") or {}).get("id")
    if not thread:
        return {"thread_id": None}
    sessions = Path(os.path.expanduser("~/.codex/sessions"))
    rollout = next(iter(sorted(sessions.rglob(f"rollout-*{thread}.jsonl"))), None)
    metrics = {"thread_id": thread, "rollout": str(rollout) if rollout else None}
    if not rollout:
        return metrics
    usage, requests, calls = None, 0, 0
    for line in rollout.read_text(encoding="utf-8", errors="replace").splitlines():
        payload = (json.loads(line).get("payload") or {}) if line.strip() else {}
        if payload.get("type") == "token_count" and payload.get("info"):
            usage, requests = payload["info"]["total_token_usage"], requests + 1
        if payload.get("type") in {"custom_tool_call", "function_call"}:
            calls += 1
    if usage:
        metrics.update(
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            uncached_input_tokens=usage["input_tokens"] - usage["cached_input_tokens"],
            output_tokens=usage["output_tokens"],
            model_requests=requests,
            tool_calls=calls,
        )
    return metrics


def hook_metrics(log_path):
    rows = []
    if log_path.exists():
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    tool_rows = [r for r in rows if r.get("event") == "PostToolUse"]
    shortened = [r for r in tool_rows if r.get("action") in {"filter", "observe"}]
    return {
        "post_tool_use": len(tool_rows),
        "large_outputs": len(shortened),
        "large_output_bytes": sum(r["bytes"] for r in shortened),
        "replacement_bytes": sum(r["replacement_bytes"] for r in shortened),
        "saved_output_reads": sum(bool(SAVED_OUTPUT.search(r.get("command", ""))) for r in rows),
        "hook_errors": sum(r.get("event") == "error" for r in rows),
    }


def report(destination):
    destination, suite = load(destination)
    rows = []
    for trial in suite["trials"]:
        directory = Path(trial["directory"])
        if not (directory / "run.json").exists():
            continue
        run_record = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        grade_path = directory / "grade.json"
        rows.append({
            **{k: trial[k] for k in ("id", "task", "condition", "repeat", "order")},
            "wall_seconds": run_record["wall_seconds"],
            "exit_code": run_record["exit_code"],
            "passed": json.loads(grade_path.read_text(encoding="utf-8"))["passed"]
            if grade_path.exists() else None,
            **session_metrics(directory / "events.jsonl"),
            **hook_metrics(directory / "h/hook-log.jsonl"),
        })  # fmt: skip
    summary = {}
    for condition in CONDITIONS:
        group = [r for r in rows if r["condition"] == condition]

        def median(key, group=group):
            values = [r[key] for r in group if isinstance(r.get(key), (int, float))]
            return statistics.median(values) if values else None

        summary[condition] = {
            "runs": len(group),
            "passed": sum(r["passed"] is True for r in group),
            **{
                f"median_{key}": median(key)
                for key in (
                    "input_tokens",
                    "uncached_input_tokens",
                    "output_tokens",
                    "model_requests",
                    "tool_calls",
                    "wall_seconds",
                )
            },  # fmt: skip
            "large_outputs": sum(r.get("large_outputs", 0) for r in group),
            "saved_output_reads": sum(r.get("saved_output_reads", 0) for r in group),
        }
    result = {"created_at": now(), "trials": rows, "summary": summary}
    write_json(destination / "report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(prog="hook_benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run", "grade", "report"):
        command = commands.add_parser(name)
        command.add_argument("--directory", required=True)
        if name in {"run", "grade"}:
            command.add_argument("--trial", required=True)
        if name == "run":
            command.add_argument("--model", required=True)
            command.add_argument("--codex")
            command.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        suite = prepare(args.directory)
        result = {
            "trials": len(suite["trials"]),
            "tasks": {
                name: {
                    "reference_passed": task["reference_suite"]["passed"],
                    "seed_suite_passed": task["seed_suite"]["passed"],
                    "seed_suite_output_bytes": task["seed_suite"]["output_bytes"],
                    "seed_hidden_passed": task["seed_hidden_passed"],
                }
                for name, task in suite["tasks"].items()
            },
        }
    elif args.command == "run":
        result = run(args.directory, args.trial, args.model, args.codex, args.execute)
    elif args.command == "grade":
        result = grade(args.directory, args.trial)
    else:
        result = report(args.directory)["summary"]
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
