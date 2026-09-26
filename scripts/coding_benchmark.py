"""Frozen seeded repair trials. No model launch, production writes or promotion."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from jev_context.common import digest, dumps, now

ROOT = Path(__file__).resolve().parents[1]
TASKS = {
    "json": {
        "file": "cli.py",
        "before": 'value = strict_loads(raw.decode("utf-8"))',
        "after": 'value = json.loads(raw.decode("utf-8"))',
        "requirement": "Reject duplicate keys at every nesting depth and non-finite JSON numbers before processing a CLI request. Preserve valid UTF-8 object input and the existing byte limit. Keep the public function and CLI contracts unchanged.",
    },
    "availability": {
        "file": "coordination.py",
        "before": 'items = [i for i in inventory["items"] if i["available"]]',
        "after": 'items = list(inventory["items"])',
        "requirement": "Never send an unavailable capability to model inference or include it in selected results. Report missing required capabilities. Preserve available mandatory tools, freshness checks and incomplete-inventory behavior.",
    },
    "budget": {
        "file": "budget.py",
        "before": 'if item.get("role") != "required_evidence" and not item.get("mandatory")',
        "after": 'if not item.get("mandatory")',
        "requirement": "Keep required evidence when reducing a response to its byte budget. Remove only optional evidence; if mandatory content cannot fit, return explicit insufficient. Preserve mandatory capabilities, Korean constraints and whole MCP envelope accounting.",
    },
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tracked_files(path):
    return {
        p.relative_to(path).as_posix(): digest(p.read_bytes())
        for p in sorted(path.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }


def run_checks(project, check, receipt):
    receipt.parent.mkdir(parents=True, exist_ok=True)
    xml = receipt.with_suffix(".xml")
    scratch_root = ROOT / ".t"
    scratch_root.mkdir(exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="c-", dir=scratch_root)
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        scratch,
        "--import-mode=importlib",
        "--noconftest",
        "-c",
        str(project / "pytest.ini"),
        "-o",
        "addopts=",
        "--junitxml",
        str(xml),
        str(check),
    ]
    tick = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=40,
        env={
            **os.environ,
            "PYTHONPATH": str(project / "src"),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "",
        },
    )
    cases = list(ET.parse(xml).getroot().iter("testcase")) if xml.exists() else []
    failures = sum(c.find("failure") is not None for c in cases)
    errors = sum(c.find("error") is not None for c in cases)
    skipped = sum(c.find("skipped") is not None for c in cases)
    result = {
        "command": command,
        "cwd": str(project),
        "exit_code": completed.returncode,
        "elapsed_ms": (time.monotonic() - tick) * 1000,
        "tests": len(cases),
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "passed": completed.returncode == 0 and bool(cases) and not (failures or errors or skipped),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    write_json(receipt, result)
    return result


def prepare_suite(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "checks").mkdir()
    report = {
        "created_at": now(),
        "provenance": "agent_authored_seeded_repairs",
        "human_reviewed": False,
        "promotion_eligible": False,
        "tasks": [],
        "trials": [],
    }
    for name, task in TASKS.items():
        reference = destination / "references" / name
        package = reference / "src/jev_context"
        package.mkdir(parents=True)
        (reference / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        for source in (ROOT / "src/jev_context").iterdir():
            if source.suffix in {".py", ".json"}:
                shutil.copy2(source, package / source.name)
        check = destination / "checks" / f"check_{name}.py"
        shutil.copy2(ROOT / "evaluations/coding" / check.name, check)
        baseline = run_checks(reference, check, destination / "receipts" / f"{name}-reference.json")
        if not baseline["passed"]:
            raise RuntimeError(f"Reference implementation failed: {name}")
        target = package / task["file"]
        original = target.read_text(encoding="utf-8")
        if original.count(task["before"]) != 1:
            raise ValueError(f"Seed location changed: {name}")
        seeded = original.replace(task["before"], task["after"], 1)
        first = None
        for condition in ("off", "observe"):
            trial = destination / "trials" / f"{name}-{condition}"
            shutil.copytree(
                reference, trial, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
            )
            (trial / "src/jev_context" / task["file"]).write_text(seeded, encoding="utf-8")
            instruction = (
                f"# Repair task\n\n{task['requirement']}\n\n"
                f"Only modify src/jev_context/{task['file']}. "
                "Do not read outside this trial directory, the original repository, sibling trials, "
                "reference implementations or grader files. Do not use MCP, external network, other agents, "
                "or project memory. You may inspect this snapshot and run inline checks without creating files. "
                "Report the changed path and reasoning; do not claim access to hidden tests.\n"
            )
            (trial / "TASK.md").write_text(instruction, encoding="utf-8")
            report["trials"].append(
                {
                    "id": f"{name}-{condition}",
                    "task": name,
                    "condition": condition,
                    "directory": str(trial),
                    "allowed_file": f"src/jev_context/{task['file']}",
                    "initial_files": tracked_files(trial),
                    "check_sha256": digest(check.read_bytes()),
                }
            )
            first = first or trial
        broken = run_checks(first, check, destination / "receipts" / f"{name}-seed.json")
        if broken["exit_code"] != 1 or broken["failures"] < 1 or broken["errors"]:
            raise RuntimeError(f"Seed is not an observed behavior failure: {name}")
        report["tasks"].append({"id": name, "reference": baseline, "seed": broken})
    write_json(destination / "suite.json", report)
    return report


def verify_trial(destination, trial_id):
    destination = Path(destination).resolve(strict=True)
    raw = (destination / "suite.json").read_bytes()
    seal = destination / "suite.sha256"
    if seal.exists() and seal.read_text(encoding="utf-8") != digest(raw):
        return {
            "trial_id": trial_id,
            "valid": False,
            "passed": False,
            "scope_violations": ["suite_manifest_changed"],
        }
    suite = json.loads(raw)
    record = next(t for t in suite["trials"] if t["id"] == trial_id)
    trial = Path(record["directory"]).resolve(strict=True)
    if not trial.is_relative_to(destination / "trials"):
        raise ValueError("Trial directory escaped suite")
    check = destination / "checks" / f"check_{record['task']}.py"
    current = tracked_files(trial)
    changed = sorted(
        k
        for k in record["initial_files"].keys() | current.keys()
        if record["initial_files"].get(k) != current.get(k)
    )
    invalid = [p for p in changed if p != record["allowed_file"]]
    if digest(check.read_bytes()) != record["check_sha256"]:
        invalid.append("grader_changed")
    if invalid:
        return {"trial_id": trial_id, "valid": False, "passed": False, "scope_violations": invalid}
    result = run_checks(trial, check, destination / "receipts" / f"{trial_id}-final.json")
    return {
        "trial_id": trial_id,
        "valid": True,
        "passed": result["passed"],
        "changed": changed,
        "check": result,
        "host_input_tokens": None,
        "host_output_tokens": None,
        "host_cost": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "verify"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--trial")
    args = parser.parse_args()
    if args.command == "prepare":
        report = prepare_suite(args.directory)
        print(dumps({"trials": len(report["trials"]), "directory": str(args.directory)}))
    else:
        if not args.trial:
            parser.error("verify requires --trial")
        result = verify_trial(args.directory, args.trial)
        print(dumps(result))
        return 0 if result["valid"] and result["passed"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
