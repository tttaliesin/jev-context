"""Second diagnostic: run with python -m scripts.evaluate_ollaya_tuning. No production writes."""

import argparse
import ctypes
import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path

from jev_context.engines import SemifOpenVINO, convert
from scripts.evaluate_ollaya import Client, process_snapshot, question_contract, read, save, sha

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluations/ollaya-tuning"
MODELS = {"laya:multilingual": 4, "decider:0.8b": 6, "decider:2b": 12}
LIMIT_SECONDS = 15


def memory_gib():
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong)
            for name in (
                "physical",
                "available",
                "page",
                "available_page",
                "virtual",
                "available_virtual",
                "extended",
            )
        ]

    value = Memory()
    value.length = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        raise OSError("Cannot inspect available memory")
    return value.available / (1024**3)


def input_state(state, mode):
    if mode == "json":
        return state
    # Preserve every supplied field and its exact value; remove only literal duplicate goal.
    fields = []
    for name, value in state.items():
        if name == "goal" and value == state.get("query"):
            continue
        if name == "query" and value == state.get("claim"):
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        fields.append(f"[{name.upper()}]\n{text}\n[/{name.upper()}]")
    return "\n\n".join(fields)


def combine(purpose, answers):
    choices = {name: answer["choice"] for name, answer in answers.items()}
    score = min(answer["raw_distribution"][answer["choice"]] for answer in answers.values())
    if purpose == "relevance":
        choice = {"yes": "relevant", "no": "irrelevant"}.get(
            choices["related"], "insufficient_evidence"
        )
    elif purpose == "evidence_relation":
        positive = [name for name, value in choices.items() if value == "yes"]
        choice = (
            positive[0]
            if len(positive) == 1 and "insufficient_evidence" not in choices.values()
            else "insufficient_evidence"
        )
    else:
        if choices["unspecified"] == "yes":
            choice = "insufficient_evidence"
        elif choices["blocked"] == "yes":
            choice = "unfit"
        elif "insufficient_evidence" in choices.values():
            choice = "insufficient_evidence"
        elif choices["helpful"] == "yes":
            choice = "fit"
        else:
            choice = "insufficient_evidence"
    return {
        "choice": choice,
        "decision_score": score,
        "subanswers": answers,
        "composition": choices,
    }


def evaluate_case(client, model, candidate, case):
    purpose = case["purpose"]
    state = input_state(case["state"], candidate["input"])
    wire = candidate["questions"][purpose]
    group = wire if candidate.get("decomposed") else {purpose: wire}
    answers = {}
    deadline = time.perf_counter() + LIMIT_SECONDS
    for name, question in group.items():
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("Whole-case deadline exhausted")
        raw = client.call(
            "/v1/systemone",
            {"model": model, "state": state, "questions": {name: question}},
            timeout=remaining,
        )
        if raw.get("model") != model or raw.get("state_truncated"):
            raise ValueError("Changed model identity or truncated state")
        contract = question_contract(purpose, question)
        contract["id"] = name
        answers[name] = convert(raw, [contract], "Ollaya normalized maximum probability")[0]
    if candidate.get("decomposed"):
        return combine(purpose, answers)
    answer = answers[purpose]
    mapping = candidate.get("label_maps", {}).get(purpose, {})
    distribution = {
        mapping.get(key, key): value for key, value in answer["raw_distribution"].items()
    }
    choice = mapping.get(answer["choice"], answer["choice"])
    return {"choice": choice, "decision_score": distribution[choice], "probabilities": distribution}


def summarize(rows, threshold=0):
    observed = [r for r in rows if r["status"] == "observed"]
    accepted = [
        r
        for r in observed
        if r["choice"] != "insufficient_evidence" and r["decision_score"] >= threshold
    ]
    latency = sorted(r["latency_ms"] for r in observed)
    probabilistic = [r for r in observed if "probabilities" in r]
    return {
        "count": len(rows),
        "observed": len(observed),
        "correct": sum(r.get("choice") == r["expected"] for r in rows),
        "critical_errors": sum(r["critical"] and r.get("choice") != r["expected"] for r in rows),
        "errors": sum(r["status"] == "error" for r in rows),
        "not_run": sum(r["status"] == "not_run" for r in rows),
        "accepted": len(accepted),
        "accepted_correct": sum(r["choice"] == r["expected"] for r in accepted),
        "critical_wrong_accepts": sum(
            r["critical"] and r["choice"] != r["expected"] for r in accepted
        ),
        "p50_ms": statistics.median(latency) if latency else None,
        "p95_ms": latency[math.ceil(len(latency) * 0.95) - 1] if latency else None,
        "within_2s": sum(r["latency_ms"] <= 2000 for r in observed),
        "within_6s": sum(r["latency_ms"] <= 6000 for r in observed),
        "nll": statistics.mean(
            -math.log(max(r["probabilities"][r["expected"]], 1e-8)) for r in probabilistic
        )
        if len(probabilistic) == len(rows)
        else None,
        "confusion": dict(
            Counter(f"{r['purpose']}:{r['expected']}->{r.get('choice', r['status'])}" for r in rows)
        ),
        "by_purpose": {
            p: {
                "count": sum(r["purpose"] == p for r in rows),
                "correct": sum(
                    r["purpose"] == p and r.get("choice") == r["expected"] for r in rows
                ),
            }
            for p in sorted({r["purpose"] for r in rows})
        },
    }


def select_candidate(runs):
    eligible = {
        name: run
        for name, run in runs.items()
        if run["rows"]
        and all(r["split"] == "development" and r["status"] == "observed" for r in run["rows"])
    }
    if not eligible:
        return {"status": "no_complete_candidate"}
    name = min(
        eligible,
        key=lambda n: (
            eligible[n]["summary"]["critical_errors"],
            -eligible[n]["summary"]["correct"],
            eligible[n]["summary"]["p95_ms"],
            n,
        ),
    )
    rows = eligible[name]["rows"]
    threshold = 1.01
    for value in (0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
        metrics = summarize(rows, value)
        if (
            metrics["accepted"] >= 20
            and metrics["accepted_correct"] / metrics["accepted"] >= 0.95
            and metrics["critical_wrong_accepts"] == 0
        ):
            threshold = value
            break
    return {
        "status": "selected",
        "candidate": name,
        "threshold": threshold,
        "summary_with_threshold": summarize(rows, threshold),
    }


def collect(cases, evaluate, checkpoint):
    rows, failures = [], 0
    for case in cases:
        row = {key: case[key] for key in ("id", "split", "purpose", "expected", "critical")}
        if failures >= 3:
            row.update(status="not_run", reason="three_consecutive_errors")
        else:
            started = time.perf_counter()
            try:
                if memory_gib() < 1.5:
                    failures = 3
                    raise MemoryError("Available RAM below 1.5 GiB; stop this run")
                row.update(evaluate(case))
                if time.perf_counter() - started > LIMIT_SECONDS:
                    raise TimeoutError("Whole-case deadline exceeded")
                row["status"] = "observed"
                failures = 0
            except Exception as exc:
                for key in (
                    "choice",
                    "probabilities",
                    "decision_score",
                    "subanswers",
                    "composition",
                ):
                    row.pop(key, None)
                row.update(status="error", reason=f"{type(exc).__name__}: {exc}")
                failures += 1
            row["latency_ms"] = (time.perf_counter() - started) * 1000
        rows.append(row)
        save(checkpoint, rows)
        print(f"{checkpoint.stem}: {len(rows)}/{len(cases)} {row['status']}", flush=True)
    return {"rows": rows, "summary": summarize(rows)}


def artifact_identity(store, model):
    family, tag = model.split(":")
    manifest = store / f"manifests/ollaya.dev/library/{family}/{tag}"
    data = read(manifest)
    hashes = {"manifest": sha(manifest)}
    calibration = None
    for layer in [data["config"], *data["layers"]]:
        blob = store / "blobs" / layer["digest"].replace(":", "-")
        actual = sha(blob)
        if actual != layer["digest"].split(":")[1] or blob.stat().st_size != layer["size"]:
            raise ValueError("Model blob integrity mismatch")
        hashes[blob.name] = actual
        if layer["mediaType"] == "application/vnd.ollaya.calibration":
            calibration = read(blob)
    return {"hashes": hashes, "calibration": calibration}


def load_frozen():
    frozen = read(FIXTURES / "frozen.json")
    for name, expected in frozen["files"].items():
        if sha(FIXTURES / name) != expected:
            raise ValueError(f"Frozen file changed: {name}")
    return frozen, read(FIXTURES / "candidates.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=["prepare", "development", "select", "validation", "semif"]
    )
    parser.add_argument("--model", choices=list(MODELS), default="laya:multilingual")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11437")
    parser.add_argument("--store", type=Path, default=ROOT / ".local/ollaya-evaluation/models")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/ollaya-tuning/results")
    parser.add_argument("--profile", type=Path, default=ROOT / ".local/semif-ov-profile.json")
    parser.add_argument("--server-pid", type=int, required=True)
    args = parser.parse_args()
    frozen, candidates = load_frozen()
    client = Client(args.endpoint)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    suffix = args.model.replace(":", "-") if args.stage in {"development", "validation"} else ""
    stage = f"{args.stage}-{suffix}".rstrip("-")
    with (output / f"{stage}.started").open("x", encoding="utf-8") as marker:
        marker.write(str(time.time()))
    report = {
        "stage": stage,
        "frozen": frozen,
        "promotion_eligible": False,
        "created_at": time.time(),
    }
    try:
        if args.stage == "prepare":
            if (
                client.call("/api/version")["version"] != "0.7.3"
                or client.call("/api/ps")["models"]
            ):
                raise ValueError("Requires an idle Ollaya 0.7.3 server")
            report["models"] = {}
            for model, minimum in MODELS.items():
                available = memory_gib()
                if available < minimum:
                    report["models"][model] = {
                        "status": "excluded",
                        "reason": "memory_precondition",
                        "available_gib": available,
                        "required_gib": minimum,
                    }
                else:
                    try:
                        if model != "laya:multilingual":
                            print(f"pull {model}", flush=True)
                            client.call(
                                "/api/pull", {"model": model, "stream": False}, timeout=1800
                            )
                        report["models"][model] = {
                            "status": "ready",
                            "available_gib": available,
                            "identity": artifact_identity(args.store, model),
                        }
                    except Exception as exc:
                        report["models"][model] = {"status": "unavailable", "reason": str(exc)}
                save(output / "prepare.json", report)
        elif args.stage == "select":
            selections = {}
            for model, metadata in read(output / "prepare.json")["models"].items():
                if metadata["status"] != "ready":
                    selections[model] = metadata
                    continue
                path = output / f"development-{model.replace(':', '-')}.json"
                data = read(path)
                selections[model] = {
                    **select_candidate(data.get("runs", {})),
                    "development_sha256": sha(path),
                }
            report["selections"] = selections
        else:
            prepared = read(output / "prepare.json")
            if prepared["frozen"] != frozen:
                raise ValueError("Preparation used different fixtures")
            if args.stage == "semif":
                read(output / "select.json")  # No new final cases before selection.
                if client.call("/api/ps")["models"]:
                    raise RuntimeError("Ollaya must be unloaded")
                engine = SemifOpenVINO(read(args.profile))
                started = time.perf_counter()
                try:
                    engine.prepare()
                    report.update(
                        preparation_seconds=time.perf_counter() - started,
                        fingerprint=engine.fingerprint,
                    )
                    report["memory_loaded"] = process_snapshot(engine.process.pid)

                    def evaluate(case):
                        answer = engine.evaluate(
                            {
                                "evaluation_id": case["id"],
                                "profile_fingerprint": engine.fingerprint,
                                "deadline_ms": LIMIT_SECONDS * 1000,
                                "state": case["state"],
                                "questions": [
                                    question_contract(
                                        case["purpose"],
                                        candidates["a_original"]["questions"][case["purpose"]],
                                    )
                                ],
                            }
                        )["answers"][0]
                        return {
                            "choice": answer["choice"],
                            "probabilities": answer["raw_distribution"],
                            "decision_score": answer["raw_distribution"][answer["choice"]],
                        }

                    report["run"] = collect(
                        read(FIXTURES / "validation.json")["cases"],
                        evaluate,
                        output / "rows-semif.json",
                    )
                    worker_pid = engine.process.pid
                finally:
                    engine.close()
                report["after_close"] = process_snapshot(worker_pid)
            else:
                metadata = prepared["models"][args.model]
                if metadata["status"] != "ready":
                    report["excluded"] = metadata
                elif memory_gib() < MODELS[args.model]:
                    report["excluded"] = {
                        "reason": "memory_precondition_at_load",
                        "available_gib": memory_gib(),
                    }
                else:
                    if artifact_identity(args.store, args.model) != metadata["identity"]:
                        raise ValueError("Model bytes changed after preparation")
                    if client.call("/api/ps")["models"]:
                        raise RuntimeError("Another model is still loaded")
                    if args.stage == "development":
                        names = (
                            list(candidates)
                            if args.model == "laya:multilingual"
                            else ["c_korean_fields", "d_english_fields"]
                        )
                    else:
                        selection = read(output / "select.json")
                        chosen = selection["selections"][args.model]
                        if chosen["status"] != "selected":
                            raise ValueError("No complete development candidate")
                        development_path = (
                            output / f"development-{args.model.replace(':', '-')}.json"
                        )
                        if sha(development_path) != chosen["development_sha256"]:
                            raise ValueError("Development results changed")
                        names = list(
                            dict.fromkeys(
                                (["a_original"] if args.model == "laya:multilingual" else [])
                                + [chosen["candidate"]]
                            )
                        )
                        report["selection_sha256"] = sha(output / "select.json")
                    started = time.perf_counter()
                    try:
                        client.call(
                            "/api/decide", {"model": args.model, "keep_alive": -1}, timeout=300
                        )
                        report["preparation_seconds"] = time.perf_counter() - started
                        if memory_gib() < 1.5:
                            raise MemoryError("Post-load available memory below 1.5 GiB")
                        report["memory_loaded"] = process_snapshot(args.server_pid)
                        report["runs"] = {}
                        cases = read(FIXTURES / f"{args.stage}.json")["cases"]
                        for name in names:
                            report["runs"][name] = collect(
                                cases,
                                lambda case, name=name: evaluate_case(
                                    client, args.model, candidates[name], case
                                ),
                                output / f"rows-{stage}-{name}.json",
                            )
                            if args.stage == "validation":
                                report["runs"][name]["with_threshold"] = summarize(
                                    report["runs"][name]["rows"], chosen["threshold"]
                                )
                            save(output / f"{stage}.json", report)
                            if report["runs"][name]["summary"]["errors"]:
                                break
                    finally:
                        client.unload(args.model)
                    report["after_unload"] = client.call("/api/ps")
    except Exception as exc:
        report["stage_error"] = f"{type(exc).__name__}: {exc}"
        save(output / f"{stage}.json", report)
        raise
    save(output / f"{stage}.json", report)
    print(
        json.dumps(
            {
                "stage": stage,
                "summaries": {n: r["summary"] for n, r in report.get("runs", {}).items()},
                "selections": report.get("selections"),
                "excluded": report.get("excluded"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
