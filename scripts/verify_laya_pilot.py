"""Compare reloaded PyTorch checkpoint with isolated Ollaya; never evaluates accuracy."""

import argparse
import json
import time
from pathlib import Path

from scripts.evaluate_ollaya import Client, process_snapshot, read, save, sha

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, default=ROOT / ".local/laya-finetuning/pilot")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11438")
    parser.add_argument("--server-pid", type=int, required=True)
    args = parser.parse_args()
    output = args.pilot / "parity.json"
    if output.exists():
        raise FileExistsError("Parity result already exists")
    reference = read(args.pilot / "reload.json")
    packaged = read(args.pilot / "package.json")
    if reference["checkpoint_sha256"] != packaged["checkpoint_sha256"]:
        raise ValueError("Reference and package checkpoints differ")
    client = Client(args.endpoint)
    if client.call("/api/version")["version"] != "0.7.3" or client.call("/api/ps")["models"]:
        raise ValueError("Requires idle isolated Ollaya 0.7.3")
    report = {
        "model": packaged["model"],
        "checkpoint_sha256": reference["checkpoint_sha256"],
        "reference_sha256": sha(args.pilot / "reload.json"),
        "rows": [],
        "quality_evaluation": False,
        "promotion_eligible": False,
    }
    try:
        started = time.perf_counter()
        client.call("/api/decide", {"model": packaged["model"], "keep_alive": -1}, timeout=300)
        report["load_seconds"] = time.perf_counter() - started
        report["memory_loaded"] = process_snapshot(args.server_pid)
        for row in reference["predictions"]:
            started = time.perf_counter()
            raw = client.call(
                "/v1/systemone",
                {
                    "model": packaged["model"],
                    "state": row["state"],
                    "questions": {row["purpose"]: row["question"]},
                },
                timeout=15,
            )
            if raw.get("model") != packaged["model"] or raw.get("state_truncated"):
                raise ValueError("Model mismatch or truncated state")
            actual = raw["answers"][row["purpose"]]
            expected = row["answer"]
            if set(actual["probabilities"]) != set(expected["probabilities"]):
                raise ValueError("Label set changed")
            error = max(
                abs(actual["probabilities"][k] - expected["probabilities"][k])
                for k in expected["probabilities"]
            )
            report["rows"].append(
                {
                    "id": row["id"],
                    "choice_match": actual["choice"] == expected["choice"],
                    "maximum_probability_error": error,
                    "actual": actual,
                    "latency_ms": (time.perf_counter() - started) * 1000,
                }
            )
            save(output, report)
        report["passed"] = len(report["rows"]) == 12 and all(
            r["choice_match"] and r["maximum_probability_error"] <= 0.005 for r in report["rows"]
        )
    except Exception as exc:
        report.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        client.unload(packaged["model"])
        report["after_unload"] = client.call("/api/ps")
        save(output, report)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "count": len(report["rows"]),
                "max_error": max(r["maximum_probability_error"] for r in report["rows"]),
            }
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
