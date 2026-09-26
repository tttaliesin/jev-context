"""Freeze paired policy-only contexts with real shadow judgments, never source solutions."""

import argparse
import json
import time
from pathlib import Path

from jev_context.common import digest, now, uid
from jev_context.judgment import TEMPLATE_REVISION, question
from jev_context.modal_engine import ModalOpenJev
from jev_context.policy import Config
from scripts.coding_benchmark import ROOT, TASKS, tracked_files, write_json

EXCERPTS = (
    ("cli-policy", "docs/judgment-revision.md", 93, 93),
    ("availability-policy", "docs/judgment-revision.md", 68, 68),
    ("evidence-policy", "docs/implementation-v2.md", 13, 20),
    ("inventory-policy", "docs/implementation-v2.md", 104, 105),
)


def policy_evidence():
    evidence = []
    for key, path, first, last in EXCERPTS:
        raw = (ROOT / path).read_bytes()
        evidence.append(
            {
                "id": key,
                "origin": {
                    "path": path,
                    "sha256": digest(raw),
                    "start_line": first,
                    "end_line": last,
                },
                "text": "\n".join(raw.decode("utf-8").splitlines()[first - 1 : last]),
            }
        )
    return evidence


def seal_contexts(destination, evidence, judgments, metadata):
    """Reject incomplete/model-free observe data before changing any frozen trial."""
    destination = Path(destination).resolve(strict=True)
    suite_path = destination / "suite.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    if "contexts" in suite:
        raise ValueError("Contexts are already frozen")
    ids = [item["id"] for item in evidence]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Evidence IDs must be nonempty and unique")
    for task in TASKS:
        rows = judgments.get(task, [])
        if [row["id"] for row in rows] != ids or any(
            row["result"].get("status") != "observed" for row in rows
        ):
            raise ValueError("Every observe candidate needs an actual observed judgment")
    for trial in suite["trials"]:
        if tracked_files(Path(trial["directory"])) != trial["initial_files"]:
            raise ValueError("Trial changed before context freeze")
    for trial in suite["trials"]:
        context = {
            "goal": TASKS[trial["task"]]["requirement"],
            "evidence": evidence,
            "judgments": [
                {"id": row["id"], "answers": row["result"]["answers"]}
                for row in judgments[trial["task"]]
            ]
            if trial["condition"] == "observe"
            else [],
            "interpretation": "Original policy excerpts are evidence. Model judgments, if present, are unverified shadow observations. They do not remove evidence or grant authority.",
        }
        path = Path(trial["directory"]) / "CONTEXT.json"
        write_json(path, context)
        trial["context_sha256"] = digest(path.read_bytes())
        trial["context_bytes"] = len(path.read_bytes())
        trial["initial_files"] = tracked_files(Path(trial["directory"]))
    suite["contexts"] = {"frozen_at": now(), **metadata}
    write_json(destination / "context-judgments.json", judgments)
    suite["contexts"]["judgments_sha256"] = digest(
        (destination / "context-judgments.json").read_bytes()
    )
    write_json(suite_path, suite)
    (destination / "suite.sha256").write_text(digest(suite_path.read_bytes()), encoding="utf-8")
    return suite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work-id", required=True)
    args = parser.parse_args()
    config = Config.load(args.config)
    profile = json.loads(Path(config.engine["profile_file"]).read_text(encoding="utf-8"))
    engine = ModalOpenJev(profile)
    engine.prepare()
    if engine.state != "shadow":
        raise RuntimeError("Start the matching work session and wait until ready")
    evidence = policy_evidence()
    plan = {
        "created_at": now(),
        "evidence": evidence,
        "tasks": {key: value["requirement"] for key, value in TASKS.items()},
        "profile_fingerprint": engine.fingerprint,
        "template_revision": TEMPLATE_REVISION,
        "purposes": ["relevance", "presentation"],
        "integration": "direct_adapter_policy_annotations_not_full_MCP_context_pipeline",
    }
    # Exact inputs are fixed before inference, and existing plans are never overwritten.
    with (args.directory / "contexts.plan.json").open("x", encoding="utf-8") as output:
        json.dump(plan, output, ensure_ascii=False, indent=2)
    judgments, times = {}, {}
    for task, spec in TASKS.items():
        requests = [
            {
                "evaluation_id": uid("eval"),
                "profile_fingerprint": engine.fingerprint,
                "project_id": config.project_id,
                "work_id": args.work_id,
                "deadline_ms": 2000,
                "state": {
                    "goal": spec["requirement"],
                    "query": spec["requirement"],
                    "candidate": item["text"],
                },
                "questions": [question(purpose, "ko") for purpose in plan["purposes"]],
            }
            for item in evidence
        ]
        tick = time.monotonic()
        results = engine.evaluate_many(requests)
        times[task] = (time.monotonic() - tick) * 1000
        judgments[task] = [
            {"id": item["id"], "result": result}
            for item, result in zip(evidence, results, strict=True)
        ]
        write_json(args.directory / "context-observations.json", judgments)
    suite = seal_contexts(
        args.directory,
        evidence,
        judgments,
        {"plan": plan, "inference_ms": times, "host_input_tokens": None},
    )
    print(json.dumps({"trials": len(suite["trials"]), "inference_ms": times}))


if __name__ == "__main__":
    main()
