"""Frozen paired prompt comparison; development/validation results never enable promotion."""

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from jev_context.common import DomainError, digest, dumps, now, uid
from jev_context.judgment import OPTIONS, TEMPLATE_REVISION, question
from jev_context.modal_engine import ModalOpenJev
from jev_context.policy import Config

ROOT = Path(__file__).resolve().parents[1]


def load_comparison(baseline_path, candidate_path, dataset_paths):
    templates = {}
    manifests = {}
    for variant, path in (("baseline", baseline_path), ("candidate", candidate_path)):
        if path is None:
            templates[variant] = {p: question(p) for p in OPTIONS}
            manifests[variant] = {"template_revision": TEMPLATE_REVISION, "origin": "runtime"}
        else:
            raw = path.read_bytes()
            data = json.loads(raw)
            templates[variant] = data["questions"]
            manifests[variant] = {
                "template_revision": data["template_revision"],
                "path": str(path),
                "sha256": digest(raw),
            }
        if set(templates[variant]) != set(OPTIONS):
            raise ValueError("Comparison templates must contain every current purpose")
        for purpose, value in templates[variant].items():
            if (
                value["purpose"] != purpose
                or value["id"] != purpose
                or set(value["options"]) != set(question(purpose)["options"])
            ):
                raise ValueError("Comparison must preserve purpose IDs and label contracts")
    cases, datasets, seen = [], {}, set()
    for path in dataset_paths:
        raw = path.read_bytes()
        data = json.loads(raw)
        if path.name in datasets:
            raise ValueError("Dataset names must be unique")
        datasets[path.name] = {
            "sha256": digest(raw),
            "split": data["split"],
            "cases": len(data["cases"]),
        }
        for case in data["cases"]:
            if case["id"] in seen:
                raise ValueError("Duplicate case ID would corrupt repeat statistics")
            if case["expected"] not in templates["candidate"][case["purpose"]]["options"]:
                raise ValueError("Expected label is outside the question contract")
            seen.add(case["id"])
            cases.append({**case, "cohort": data["split"]})
    if not cases:
        raise ValueError("An empty comparison cannot establish quality")
    return templates, manifests, cases, datasets


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["cohort"], row["variant"], row["language"])].append(row)
    summaries = []
    for (cohort, variant, language), group in groups.items():
        cases = defaultdict(list)
        for row in group:
            cases[row["id"]].append(row)
        summaries.append(
            {
                "cohort": cohort,
                "variant": variant,
                "language": language,
                "cases": len(cases),
                "attempts": len(group),
                "correct": sum(row["correct"] for row in group),
                "always_correct_cases": sum(
                    all(r["correct"] for r in case) for case in cases.values()
                ),
                "unstable_cases": sum(
                    len({r["choice"] for r in case}) > 1 for case in cases.values()
                ),
                "operational_abstentions": sum(row["status"] != "observed" for row in group),
                "false_fit": sum(
                    row["choice"] == "fit" and row["expected"] != "fit" for row in group
                ),
                "false_support": sum(
                    row["choice"] == "supports" and row["expected"] != "supports" for row in group
                ),
            }
        )
    return summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--wait-ready", type=int, default=0)
    parser.add_argument(
        "--baseline-templates", type=Path, default=ROOT / "models/question-templates-v1.json"
    )
    parser.add_argument("--candidate-templates", type=Path)
    parser.add_argument("--dataset", type=Path, action="append")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 3 or not 0 <= args.wait_ready <= 650:
        parser.error("repeats must be 1..3 and wait-ready 0..650 seconds")
    config = Config.load(args.config)
    profile = json.loads(Path(config.engine["profile_file"]).read_text(encoding="utf-8"))
    engine = ModalOpenJev(profile)
    variants, template_manifests, cases, datasets = load_comparison(
        args.baseline_templates,
        args.candidate_templates,
        args.dataset
        or [ROOT / "models/openjev-challenge.json", ROOT / "models/judgment-validation.json"],
    )
    report = {
        "created_at": now(),
        "profile_fingerprint": engine.fingerprint,
        "template_revision": TEMPLATE_REVISION,
        "repeats": args.repeats,
        "provenance": "agent_authored",
        "promotion_eligible": False,
        "question_hashes": {k: digest(dumps(v).encode()) for k, v in variants.items()},
        "templates": template_manifests,
        "interpretation": "Correlated repeats, paired language renderings; not human-reviewed heldout or host task quality",
        "datasets": datasets,
        "rows": [],
        "batches": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = args.output.with_suffix(".plan.json")
    if args.output.exists() or manifest.exists():
        parser.error("Choose a new output path; comparison results are immutable")
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def save():
        report["summary"] = summarize(report["rows"])
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    deadline = time.monotonic() + args.wait_ready
    while True:
        engine.prepare()
        if engine.state == "shadow":
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Matching work session is not ready")
        time.sleep(2)
    for repeat in range(args.repeats):
        order = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
        pairs = [(case, variant) for case in cases for variant in order]
        for offset in range(0, len(pairs), 8):
            chunk = pairs[offset : offset + 8]
            requests = [
                {
                    "evaluation_id": uid("eval"),
                    "profile_fingerprint": engine.fingerprint,
                    "project_id": config.project_id,
                    "work_id": args.work_id,
                    "deadline_ms": 2000,
                    "state": case["state"],
                    "questions": [
                        {**variants[variant][case["purpose"]], "language": case["language"]}
                    ],
                }
                for case, variant in chunk
            ]
            tick = time.monotonic()
            try:
                results = engine.evaluate_many(requests)
            except DomainError as exc:
                results = [{"status": "abstained", "reason": exc.code} for _ in requests]
            elapsed = (time.monotonic() - tick) * 1000
            report["batches"].append(
                {"repeat": repeat, "offset": offset, "items": len(chunk), "latency_ms": elapsed}
            )
            for (case, variant), result in zip(chunk, results, strict=True):
                answer = next(iter(result.get("answers", [])), {})
                choice = answer.get("choice")
                row = {
                    k: case[k] for k in ("id", "cohort", "purpose", "language", "expected", "tag")
                }
                row.update(
                    variant=variant,
                    repeat=repeat,
                    choice=choice,
                    correct=choice == case["expected"],
                    status=result["status"],
                    result=result,
                )
                report["rows"].append(row)
            save()
            print(
                dumps(
                    {
                        "repeat": repeat + 1,
                        "batch": offset // 8 + 1,
                        "ms": round(elapsed),
                        "completed_items": len(report["rows"]),
                    }
                ),
                flush=True,
            )
            if any(r.get("reason") in {"deadline_exceeded", "engine_unavailable"} for r in results):
                report["interrupted"] = True
                save()
                raise RuntimeError("Bounded session stopped; no automatic allocation or retry")
    times = sorted(b["latency_ms"] for b in report["batches"])
    report["latency"] = {
        "scope": "Whole batch including Windows/Modal round trip",
        "p50_ms": statistics.median(times),
        "p95_ms": times[min(len(times) - 1, int(len(times) * 0.95))],
    }
    save()
    print(dumps({"summary": report["summary"], "latency": report["latency"]}), flush=True)


if __name__ == "__main__":
    main()
