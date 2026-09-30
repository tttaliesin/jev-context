"""Compare reloaded PyTorch checkpoint with isolated Ollaya; never evaluates accuracy."""

import argparse
import contextlib
import json
import time
from pathlib import Path

from jev_context.storage import FileLock
from scripts.evaluate_ollaya import Client, process_snapshot, read, save, sha

ROOT = Path(__file__).resolve().parents[1]


def parity_count(reference):
    expected = 15 if reference.get("parity_ids_sha256") else 12
    rows = reference["predictions"]
    if len(rows) != expected or len({r["id"] for r in rows}) != expected:
        raise ValueError("Reload parity IDs/count mismatch")
    return expected


def check_reload(training, reference):
    if (
        training.get("status") != "completed"
        or reference.get("status") != "completed"
        or training["checkpoint_sha256"] != reference["checkpoint_sha256"]
    ):
        raise ValueError("Training/reload checkpoint mismatch")
    count = parity_count(reference)
    if len(training["after_fp32"]) != count:
        raise ValueError("Training/reload parity count mismatch")
    for before, after in zip(training["after_fp32"], reference["predictions"], strict=True):
        if any(before[k] != after[k] for k in ("id", "state", "purpose", "question")):
            raise ValueError("Reload parity input/order changed")
        a, b = before["answer"], after["answer"]
        if (
            a["choice"] != b["choice"]
            or set(a["probabilities"]) != set(b["probabilities"])
            or max(abs(a["probabilities"][k] - b["probabilities"][k]) for k in a["probabilities"])
            > 0.005
        ):
            raise ValueError("Reload choice/probability parity failed")
    return count


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
    count = check_reload(read(args.pilot / "train.json"), reference)
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
    leases = contextlib.ExitStack()
    leases.enter_context(FileLock(ROOT / ".local/laya-finetuning/experiment-model.lock"))
    try:
        leases.enter_context(
            FileLock(
                Path(read(ROOT / ".local/semif-ov-profile.json")["lock_root"]) / "resident.lock"
            )
        )
    except BaseException:
        leases.close()
        raise
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
        report["passed"] = len(report["rows"]) == count and all(
            r["choice_match"] and r["maximum_probability_error"] <= 0.005 for r in report["rows"]
        )
    except Exception as exc:
        report.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        try:
            client.unload(packaged["model"])
            report["after_unload"] = client.call("/api/ps")
            save(output, report)
        finally:
            leases.close()
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
