"""Repeatable real local inference. Synthetic labels never authorize promotion."""

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from jev_context.common import DomainError, digest, dumps, now, uid
from jev_context.engines import Laya, OpenJev, SemifOpenVINO
from jev_context.judgment import question


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    raw = Path(args.cases).read_bytes()
    dataset = json.loads(raw)
    families = {"laya": Laya, "semif_openvino": SemifOpenVINO}
    engine = families.get(profile["family"], OpenJev)(profile)
    started = time.monotonic()
    results, groups = [], defaultdict(list)
    try:
        engine.prepare()
        preparation = time.monotonic() - started
        for case in dataset["cases"]:
            try:
                actual = engine.evaluate(
                    {
                        "evaluation_id": uid("eval"),
                        "profile_fingerprint": engine.fingerprint,
                        "deadline_ms": 2000,
                        "state": case["state"],
                        "questions": [question(case["purpose"], case["language"])],
                    }
                )
            except DomainError as exc:
                actual = {"status": "abstained", "reason": exc.code}
            answer = next(iter(actual.get("answers", [])), {})
            row = {
                "id": case["id"],
                "purpose": case["purpose"],
                "language": case["language"],
                "expected": case["expected"],
                "critical": case.get("critical", False),
                "correct": answer.get("choice") == case["expected"],
                **actual,
            }
            results.append(row)
            groups[f"{case['purpose']}:{case['language']}"].append(row)
    finally:
        engine.close()
    gates = {}
    for name, rows in groups.items():
        gates[name] = {
            "count": len(rows),
            "correct": sum(r["correct"] for r in rows),
            "critical_regressions": sum(r["critical"] and not r["correct"] for r in rows),
            "non_abstained": sum(
                r.get("answers", [{}])[0].get("choice") not in {None, "insufficient_evidence"}
                for r in rows
            ),
            "quality_pass": False,
            "efficiency_pass": False,
            "threshold": 1.0,
        }
    latencies = sorted(r["latency_ms"] for r in results if "latency_ms" in r)
    report = {
        "created_at": now(),
        "profile_fingerprint": engine.fingerprint,
        "dataset_sha256": digest(raw),
        "dataset_provenance": dataset["provenance"],
        "split": dataset["split"],
        "preparation_seconds": preparation,
        "latency_ms": {
            "p50": statistics.median(latencies) if latencies else None,
            "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
            if latencies
            else None,
        },
        "gates": gates,
        "results": results,
        "promotion_eligible": False,
        "limitation": "Diagnostic fixtures; no human-reviewed holdout or host task efficiency comparison",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(dumps({k: v for k, v in report.items() if k != "results"}))


if __name__ == "__main__":
    main()
