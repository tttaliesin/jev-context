"""Compare saved startup probes without loading a model or importing OpenVINO."""

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def checked_rows(report, expected_count):
    issues, indexed = [], {}
    rows = report.get("results", [])
    if report.get("passed") is not True:
        issues.append("probe did not pass")
    if not isinstance(rows, list):
        return {}, [*issues, "results is not a list"]
    if len(rows) != expected_count:
        issues.append(f"expected {expected_count} results, found {len(rows)}")
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    duplicates = [key for key, count in Counter(str(key) for key in ids).items() if count > 1]
    if duplicates:
        issues.append(f"duplicate IDs: {duplicates}")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            issues.append("result without a nonempty string ID")
            continue
        case_id = row["id"]
        indexed[case_id] = row
        if not isinstance(row.get("choice"), str) or not row["choice"]:
            issues.append(f"{case_id}: missing choice")
        tokens = row.get("input_tokens")
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens <= 0:
            issues.append(f"{case_id}: invalid input token count")
        probabilities = row.get("probabilities")
        if (
            not isinstance(probabilities, dict)
            or not probabilities
            or row.get("choice") not in probabilities
            or not all(isinstance(key, str) for key in probabilities)
            or not all(finite_number(value) and 0 <= value <= 1 for value in probabilities.values())
        ):
            issues.append(f"{case_id}: invalid probabilities")
        elif not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-6):
            issues.append(f"{case_id}: probabilities do not sum to one")
    return indexed, issues


def timings(report):
    latencies = sorted(
        row["seconds"]
        for row in report.get("results", [])
        if isinstance(row, dict) and finite_number(row.get("seconds")) and row["seconds"] >= 0
    )
    return {
        "ready_seconds": report.get("ready_seconds"),
        "compile_seconds": report.get("stages_seconds", {}).get("compile"),
        "total_seconds": report.get("total_seconds"),
        "loaded_from_cache": report.get("loaded_from_cache"),
        "question_count": len(latencies),
        "question_p50_seconds": statistics.median(latencies) if latencies else None,
        "question_p95_seconds": latencies[math.ceil(len(latencies) * 0.95) - 1]
        if latencies
        else None,
        "question_total_seconds": sum(latencies) if latencies else None,
    }


def compare(baseline, candidate, *, expected_count=30, tolerance=1e-6):
    before, baseline_issues = checked_rows(baseline, expected_count)
    after, candidate_issues = checked_rows(candidate, expected_count)
    missing, extra = sorted(before.keys() - after.keys()), sorted(after.keys() - before.keys())
    common = sorted(before.keys() & after.keys())
    choices, tokens, option_sets, deltas = [], [], [], []
    for case_id in common:
        old, new = before[case_id], after[case_id]
        if old.get("choice") != new.get("choice"):
            choices.append(
                {"id": case_id, "baseline": old.get("choice"), "candidate": new.get("choice")}
            )
        if old.get("input_tokens") != new.get("input_tokens"):
            tokens.append(
                {
                    "id": case_id,
                    "baseline": old.get("input_tokens"),
                    "candidate": new.get("input_tokens"),
                }
            )
        old_prob, new_prob = old.get("probabilities"), new.get("probabilities")
        if not isinstance(old_prob, dict) or not isinstance(new_prob, dict):
            continue
        if old_prob.keys() != new_prob.keys():
            option_sets.append(case_id)
            continue
        for option in old_prob:
            if finite_number(old_prob[option]) and finite_number(new_prob[option]):
                deltas.append((abs(old_prob[option] - new_prob[option]), case_id, option))
    largest = max(deltas, default=None)
    max_delta = largest[0] if largest else None
    dataset_hashes = [baseline.get("dataset_sha256"), candidate.get("dataset_sha256")]
    dataset_match = dataset_hashes[0] == dataset_hashes[1] if all(dataset_hashes) else None
    before_time, after_time = timings(baseline), timings(candidate)
    ready_values = [before_time["ready_seconds"], after_time["ready_seconds"]]
    completed = all(report.get("passed") is True for report in (baseline, candidate))
    ready_comparable = completed and all(
        finite_number(value) and value > 0 for value in ready_values
    )
    return {
        "comparison_passed": not (
            baseline_issues
            or candidate_issues
            or missing
            or extra
            or choices
            or tokens
            or option_sets
        )
        and max_delta is not None
        and max_delta <= tolerance
        and dataset_match is not False,
        "expected_count": expected_count,
        "probability_tolerance": tolerance,
        "baseline_issues": baseline_issues,
        "candidate_issues": candidate_issues,
        "missing_ids": missing,
        "extra_ids": extra,
        "compared_ids": len(common),
        "choice_matches": len(common) - len(choices),
        "choice_mismatches": choices,
        "input_token_matches": len(common) - len(tokens),
        "input_token_mismatches": tokens,
        "option_set_mismatches": option_sets,
        "max_probability_delta": max_delta,
        "max_probability_delta_at": {"id": largest[1], "option": largest[2]} if largest else None,
        "dataset_sha256_match": dataset_match,
        "baseline_timings": before_time,
        "candidate_timings": after_time,
        "ready_seconds_change": ready_values[1] - ready_values[0] if ready_comparable else None,
        "ready_speedup_ratio": ready_values[0] / ready_values[1] if ready_comparable else None,
        "profile_changed_fields": sorted(
            key
            for key in baseline.get("profile", {}).keys() | candidate.get("profile", {}).keys()
            if baseline.get("profile", {}).get(key) != candidate.get("profile", {}).get(key)
        ),
        "runtime_version_match": baseline.get("runtime_version")
        == candidate.get("runtime_version"),
        "device_match": baseline.get("device") == candidate.get("device"),
        "limitation": (
            "Saved diagnostic outputs only; no quality promotion, lifecycle proof, or repeated-run "
            "performance estimate. Missing dataset hashes leave exact input identity unverified."
        ),
    }


def read_report(path):
    raw = path.read_bytes()
    return json.loads(raw), {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-count", type=int, default=30)
    parser.add_argument("--max-probability-delta", type=float, default=1e-6)
    args = parser.parse_args()
    if (
        args.expected_count <= 0
        or not finite_number(args.max_probability_delta)
        or args.max_probability_delta < 0
    ):
        parser.error("Expected count must be positive and probability tolerance finite/nonnegative")
    baseline, baseline_source = read_report(args.baseline)
    comparisons = []
    for path in args.candidate:
        candidate, source = read_report(path)
        comparisons.append(
            {
                "candidate": source,
                **compare(
                    baseline,
                    candidate,
                    expected_count=args.expected_count,
                    tolerance=args.max_probability_delta,
                ),
            }
        )
    report = {"baseline": baseline_source, "comparisons": comparisons}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(item["comparison_passed"] for item in comparisons) else 1


if __name__ == "__main__":
    raise SystemExit(main())
