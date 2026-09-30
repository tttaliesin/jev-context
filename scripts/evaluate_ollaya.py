"""Explicit, isolated diagnostic. Never changes the production profile or promotion policy."""

import argparse
import contextlib
import copy
import ctypes
import hashlib
import http.client
import ipaddress
import json
import math
import os
import queue
import random
import statistics
import subprocess
import threading
import time
from collections import Counter
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit

from jev_context.engines import SemifOpenVINO, convert

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluations/ollaya"
TEMPERATURES = [0.5, 0.75, 1, 1.5, 2, 3, 4]
THRESHOLDS = [0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
BASE_MODEL = "laya:multilingual"


def validate_frozen_evaluation_rows(report, cases, split, repeats):
    """Check reports against frozen inputs, not just against another report."""
    from scripts.prepare_laya_training_data import digest

    fields = ("id", "state_sha256", "purpose", "expected", "critical", "group_id")
    wanted = [
        [case[k] for k in fields] + [digest(case["question"]), split, repeat]
        for repeat in range(repeats)
        for case in cases
    ]
    actual = [
        [row[k] for k in fields] + [row["question_sha256"], row["split"], row["repeat"]]
        for row in report["rows"]
    ]
    if actual != wanted:
        raise ValueError("Evaluation rows differ from frozen input/question/label/order")
    for row, case in zip(report["rows"], cases * repeats, strict=True):
        if row["status"] == "observed" and (
            row["choice"] not in case["question"]["criteria"]
            or set(row["probabilities"]) != set(case["question"]["criteria"])
            or any(not math.isfinite(v) or not 0 <= v <= 1 for v in row["probabilities"].values())
        ):
            raise ValueError("Evaluation labels or probabilities differ from frozen question")


def summarize_learning_curve(root):
    """Pair identical cases and average seeds before resampling scenario groups."""
    root = Path(root)
    from scripts.prepare_laya_training_data import load_frozen

    dataset = root / "frozen/frozen.jsonl"
    cases = load_frozen(
        dataset, root / "frozen/prepare.json", "development", "synthetic-learning-curve"
    )
    baseline = read(root / "features/development/baseline-development.json")
    if baseline["dataset_sha256"] != sha(dataset):
        raise ValueError("Curve baseline dataset changed")
    validate_frozen_evaluation_rows(baseline, cases, "development", 1)
    identity = (
        "id",
        "state_sha256",
        "question_sha256",
        "purpose",
        "expected",
        "critical",
        "group_id",
    )
    reference = [[r[k] for k in identity] for r in baseline["rows"]]
    if len(reference) != 450 or len({r[0] for r in reference}) != 450:
        raise ValueError("Need 450 unique frozen development cases")
    purposes = ("relevance", "evidence_relation", "capability_fit")

    def score(report):
        if (
            report.get("status") != "completed"
            or report.get("split") != "development"
            or report.get("dataset_sha256") != baseline["dataset_sha256"]
        ):
            raise ValueError("Curve reports must complete on the same development dataset")
        if [[r[k] for k in identity] for r in report["rows"]] != reference or any(
            r["status"] != "observed" or r["repeat"] != 0 for r in report["rows"]
        ):
            raise ValueError("Curve input/order/failure mismatch")
        if any(sum(r["purpose"] == p for r in report["rows"]) != 150 for p in purposes):
            raise ValueError("Curve development purpose distribution changed")
        return {
            p: {
                **metrics([r for r in report["rows"] if r["purpose"] == p]),
                "p50_ms": None,
                "p95_ms": None,
                "latency_kind": "Development correctness uses cached encoder features; full inference timing is measured separately on the final test.",
            }
            for p in purposes
        }

    base_scores = score(baseline)
    settings = {}
    missing = []
    execution = read(root / "execution.json") if (root / "execution.json").exists() else {}
    executions = {run["name"]: run for run in execution.get("runs", [])}
    for size, epochs in ((450, 6), (1350, 6), (4050, 6), (450, 54), (1350, 18)):
        key = f"n{size}-e{epochs}"
        members = []
        for seed in (20260928, 20260929, 20260930):
            directory = root / f"{key}-s{seed}"
            try:
                training, report = (
                    read(directory / "train.json"),
                    read(directory / "development.json"),
                )
                if (
                    training.get("status") != "completed"
                    or training.get("diagnostic")
                    or training.get("seed") != seed
                    or len(training.get("training_ids", [])) != size
                    or training.get("epochs") != epochs
                    or training.get("data_profile") != "synthetic-learning-curve"
                    or training.get("batch_size") != 15
                    or training.get("learning_rate") != 6e-4
                    or training.get("dataset_sha256") != baseline["dataset_sha256"]
                    or training.get("max_steps") != size // 15 * epochs
                    or set(training.get("exposures", {})) != set(training["training_ids"])
                    or set(training.get("exposures", {}).values()) != {epochs}
                ):
                    raise ValueError("Incomplete or inconsistent training exposure")
                value = score(report)
                members.append(
                    dict(
                        seed=seed,
                        report=report,
                        scores=value,
                        training_seconds=training["elapsed_seconds"],
                        memory_samples=training.get("memory_samples", []),
                    )
                )
            except (OSError, ValueError, KeyError) as exc:
                recorded_execution = executions.get(f"{key}-s{seed}", {})
                try:
                    training_state = read(directory / "train.json")
                except (OSError, ValueError):
                    training_state = {}
                stored = (
                    read(directory / "development.json")
                    if (directory / "development.json").exists()
                    else {}
                )
                recorded = {r["id"]: r for r in stored.get("rows", [])}
                preserved = [
                    recorded.get(
                        r["id"],
                        {
                            **{k: r[k] for k in identity},
                            "repeat": 0,
                            "split": "development",
                            "status": "not_run",
                            "reason": "training_or_evaluation_incomplete",
                        },
                    )
                    for r in baseline["rows"]
                ]
                missing.append(
                    dict(
                        setting=key,
                        seed=seed,
                        reason=str(exc),
                        execution=recorded_execution,
                        training_status=training_state.get("status", "not_run"),
                        training_error=training_state.get("error"),
                        completed_optimizer_steps=len(training_state.get("steps", [])),
                        planned_optimizer_steps=size // 15 * epochs,
                        evaluation_rows=preserved,
                    )
                )
        if len(members) == 3:
            settings[key] = dict(size=size, epochs=epochs, members=members)

    def interval(left, right, purpose):
        by_group = {}
        changes = []
        for i, original in enumerate(baseline["rows"]):
            if purpose and original["purpose"] != purpose:
                continue
            a = statistics.mean(
                m["report"]["rows"][i]["choice"] == original["expected"] for m in left
            )
            b = statistics.mean(
                m["report"]["rows"][i]["choice"] == original["expected"] for m in right
            )
            by_group.setdefault(original["group_id"], []).append(a - b)
            if a != b:
                changes.append(
                    dict(id=original["id"], before_correct_fraction=b, after_correct_fraction=a)
                )
        groups = list(by_group)
        means = [statistics.mean(by_group[g]) for g in groups]
        rng = random.Random(20260928)
        samples = sorted(sum(rng.choices(means, k=len(means))) / len(means) for _ in range(10000))
        return dict(
            difference=statistics.mean(means),
            bootstrap_95=[samples[250], samples[9749]],
            groups=len(groups),
            distinct_cases=sum(map(len, by_group.values())),
            seed_repeats_are_independent_cases=False,
            changes=changes,
        )

    base_members = [dict(report=baseline)]
    output = {}
    for key, setting in settings.items():
        members = setting["members"]
        means = {
            p: dict(
                accuracy=statistics.mean(m["scores"][p]["correct"] / 150 for m in members),
                critical_errors=statistics.mean(m["scores"][p]["critical_errors"] for m in members),
                nll=statistics.mean(m["scores"][p]["nll_observed"] for m in members),
            )
            for p in purposes
        }
        eligible = all(
            statistics.mean(m["scores"][p]["correct"] / 150 for p in purposes)
            >= statistics.mean(base_scores[p]["correct"] / 150 for p in purposes)
            and all(
                m["scores"][p]["critical_errors"] <= base_scores[p]["critical_errors"]
                for p in purposes
            )
            for m in members
        )
        output[key] = dict(
            size=setting["size"],
            epochs=setting["epochs"],
            updates=setting["size"] // 15 * setting["epochs"],
            mean_scores=means,
            seed_scores=[
                dict(
                    seed=m["seed"],
                    scores=m["scores"],
                    training_seconds=m["training_seconds"],
                    memory_samples=m["memory_samples"],
                )
                for m in members
            ],
            eligible=eligible,
            versus_original={p: interval(members, base_members, p) for p in purposes},
        )
    largest = settings.get("n4050-e6")
    plateau = []
    saturation = {}
    improved_largest = False
    if largest:
        largest_delta = interval(largest["members"], base_members, None)
        improved_largest = largest_delta["bootstrap_95"][0] > 0
        for size in (450, 1350):
            key = f"n{size}-e6"
            if key not in settings:
                continue
            comparison = {
                p: interval(settings[key]["members"], largest["members"], p) for p in purposes
            }
            seed_consistent = all(
                abs(a["scores"][p]["correct"] - b["scores"][p]["correct"]) / 150 <= 0.02
                and a["scores"][p]["critical_errors"] <= b["scores"][p]["critical_errors"]
                for a, b in zip(settings[key]["members"], largest["members"], strict=True)
                for p in purposes
            )
            passed = (
                improved_largest
                and output[key]["eligible"]
                and seed_consistent
                and all(
                    min(comparison[p]["bootstrap_95"]) >= -0.02
                    and max(comparison[p]["bootstrap_95"]) <= 0.02
                    and output[key]["mean_scores"][p]["critical_errors"]
                    <= output["n4050-e6"]["mean_scores"][p]["critical_errors"]
                    for p in purposes
                )
            )
            saturation[key] = dict(
                passed=passed, seed_consistent=seed_consistent, comparisons=comparison
            )
            if passed:
                plateau.append(key)
    controls = {}
    for size, epochs in ((450, 54), (1350, 18)):
        key = f"n{size}-e{epochs}"
        if key in settings and largest:
            controls[key] = {
                p: interval(settings[key]["members"], largest["members"], p) for p in purposes
            }
    eligible = [k for k, v in output.items() if v["eligible"]]
    selected = (
        min(plateau, key=lambda k: output[k]["size"])
        if plateau
        else min(
            eligible,
            key=lambda k: (
                -statistics.mean(v["accuracy"] for v in output[k]["mean_scores"].values()),
                sum(v["critical_errors"] for v in output[k]["mean_scores"].values()),
                statistics.mean(v["nll"] for v in output[k]["mean_scores"].values()),
                output[k]["size"],
                output[k]["epochs"],
            ),
        )
        if eligible
        else None
    )
    selected_seed = None
    if selected:
        ordered = sorted(
            settings[selected]["members"],
            key=lambda m: (statistics.mean(m["scores"][p]["correct"] for p in purposes), m["seed"]),
        )
        selected_seed = ordered[1]["seed"]
    return dict(
        status="complete" if not missing else "incomplete",
        baseline=base_scores,
        settings=output,
        missing_runs=missing,
        saturation=saturation,
        update_controls=controls,
        selected_setting=selected,
        selected_seed=selected_seed,
        conclusion="포화 후보 확인" if plateau and not missing else "필요량 미확정",
        largest_vs_original="개선 확인"
        if improved_largest
        else "낮은 성능에서 정체 또는 개선 불확실",
        limitation="Agent-authored condition pairs share sentence generation rules; internal synthetic distribution only, not independent external or real-work validation. Group intervals condition on these three fixed seeds; individual seed results are reported separately.",
    )


def select_development_candidate(baseline, candidates):
    """Select with development only; candidates contain report and training metadata."""
    identity_fields = ("id", "state_sha256", "question_sha256", "purpose", "expected", "critical")

    def scores(report):
        if report.get("split") != "development" or report.get("status") != "completed":
            raise ValueError("Selection requires completed development reports")
        rows = report["rows"]
        count = 450 if report.get("data_profile") == "synthetic-learning-curve" else 45
        if (
            len(rows) != count
            or len({r["id"] for r in rows}) != count
            or any(
                r["split"] != "development" or r["status"] != "observed" or r["repeat"] != 0
                for r in rows
            )
        ):
            raise ValueError("Selection requires the fixed number of unique development rows")
        per_purpose = []
        for purpose in ("relevance", "evidence_relation", "capability_fit"):
            group = [r for r in rows if r["purpose"] == purpose]
            if len(group) != count // 3:
                raise ValueError("Development purpose count mismatch")
            per_purpose.append(sum(r["choice"] == r["expected"] for r in group) / (count // 3))
        probabilities = [r["probabilities"][r["expected"]] for r in rows]
        if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
            raise ValueError("Invalid development probability")
        return {
            "macro_accuracy": statistics.mean(per_purpose),
            "critical_errors": sum(r["critical"] and r["choice"] != r["expected"] for r in rows),
            "nll": statistics.mean(-math.log(max(p, 1e-8)) for p in probabilities),
        }

    base = scores(baseline)
    identities = [[r[k] for k in identity_fields] for r in baseline["rows"]]
    decisions = []
    eligible = []
    for name, candidate in candidates.items():
        report, training = candidate["report"], candidate["training"]
        reason = None
        try:
            if training.get("diagnostic") or training.get("status") != "completed":
                raise ValueError("Diagnostic or incomplete training is ineligible")
            extra = (
                training.get("data_profile") == "synthetic-experiment"
                and training["max_steps"] == 300
                and training["learning_rate"] in {1e-4, 6e-4}
            )
            if training.get("data_profile") == "synthetic-learning-curve":
                n = len(training.get("training_ids", []))
                extra = (
                    n in {450, 1350, 4050}
                    and training.get("learning_rate") == 6e-4
                    and training.get("epochs") in {450: {6, 54}, 1350: {6, 18}, 4050: {6}}[n]
                    and training.get("batch_size") == 15
                    and training.get("seed") in {20260928, 20260929, 20260930}
                )
            if not extra and (training["max_steps"], training["learning_rate"]) not in {
                (100, 1e-5),
                (300, 1e-5),
                (300, 3e-5),
            }:
                raise ValueError("Unapproved training settings")
            if (
                report["dataset_sha256"] != baseline["dataset_sha256"]
                or training["dataset_sha256"] != report["dataset_sha256"]
                or identities != [[r[k] for k in identity_fields] for r in report["rows"]]
            ):
                raise ValueError("Development inputs/order or training dataset mismatch")
            value = scores(report)
            if (
                value["macro_accuracy"] < base["macro_accuracy"]
                or value["critical_errors"] > base["critical_errors"]
            ):
                reason = "Development accuracy or critical errors regressed"
            else:
                eligible.append(
                    (
                        (
                            -value["macro_accuracy"],
                            value["critical_errors"],
                            value["nll"],
                            training["max_steps"],
                            training["learning_rate"],
                            name,
                        ),
                        name,
                    )
                )
        except (ValueError, KeyError) as exc:
            value = None
            reason = str(exc)
        decisions.append(
            {"name": name, "scores": value, "eligible": reason is None, "reason": reason}
        )
    return {
        "baseline": base,
        "candidates": decisions,
        "selected": min(eligible)[1] if eligible else None,
        "selection_split": "development",
    }


def choose_temperature(report):
    if report.get("data_profile") == "synthetic-learning-curve" and (
        report.get("status") != "completed"
        or len(report["rows"]) != 450
        or len({r["id"] for r in report["rows"]}) != 450
    ):
        raise ValueError("Curve temperature requires all 450 calibration cases")
    if report.get("split") != "calibration" or any(
        r["split"] != "calibration" or r["status"] != "observed" for r in report["rows"]
    ):
        raise ValueError("Temperature selection requires complete calibration-only results")
    scores = []
    for temperature in (0.5, 0.75, 1, 1.5, 2, 3):
        nll = statistics.mean(
            -math.log(max(scale(r["probabilities"], temperature)[r["expected"]], 1e-8))
            for r in report["rows"]
        )
        scores.append((nll, abs(temperature - 1), temperature))
    return min(scores)[2]


def check_candidate_lock(path, dataset, model_store=None):
    lock = read(path)
    if lock["dataset_sha256"] != sha(dataset):
        raise ValueError("Candidate lock dataset mismatch")
    for entry in lock["files"]:
        if sha(Path(entry["path"])) != entry["sha256"]:
            raise ValueError("Locked candidate or calibration changed")
    if model_store and str(model_store.resolve()) != lock["model_store"]:
        raise ValueError("Candidate model store differs from lock")
    return lock


def freeze_candidate(args):
    from scripts.prepare_laya_training_data import load_frozen

    load_frozen(args.dataset, args.manifest, "test", args.data_profile)
    training_report = args.checkpoint.parent.parent / "train.json"
    training = read(training_report)
    if args.data_profile == "synthetic-learning-curve":
        curve_root = getattr(args, "curve_root", None)
        if not curve_root:
            raise ValueError("Curve candidate requires its development selection root")
        selection = read(curve_root / "learning-curve.json")
        chosen = f"{selection.get('selected_setting')}-s{selection.get('selected_seed')}"
        if training_report.parent.name != chosen or selection.get("selected_setting") is None:
            raise ValueError("Candidate was not selected on curve development results")
    if (args.checkpoint.parent / "diagnostic-only.json").exists() or training.get("diagnostic"):
        raise ValueError("Diagnostic checkpoints cannot become final candidates")
    if (
        training.get("status") != "completed"
        or training.get("checkpoint_sha256") != sha(args.checkpoint)
        or training.get("dataset_sha256") != sha(args.dataset)
    ):
        raise ValueError("Candidate training did not complete against this frozen dataset")
    calibration = read(args.calibration_report)
    if calibration["dataset_sha256"] != sha(args.dataset):
        raise ValueError("Calibration dataset mismatch")
    if args.data_profile == "synthetic-learning-curve":
        gold = load_frozen(args.dataset, args.manifest, "calibration", args.data_profile)
        validate_frozen_evaluation_rows(calibration, gold, "calibration", 1)
        fields = ("id", "state_sha256", "purpose", "expected", "critical", "group_id")
        if [[r[k] for k in fields] for r in calibration["rows"]] != [
            [r[k] for k in fields] for r in gold
        ]:
            raise ValueError("Curve calibration cases/order changed")
    manifests = list((args.model_store / "manifests").rglob("pilot"))
    if len(manifests) != 1 or calibration.get("model_manifest_sha256") != sha(manifests[0]):
        raise ValueError("Calibration candidate manifest mismatch")
    temperature = choose_temperature(calibration)
    files = [
        args.checkpoint,
        args.calibration_report,
        args.manifest,
        training_report,
        Path(__file__),
        ROOT / "scripts/train_laya_pilot.py",
    ]
    if args.data_profile == "synthetic-learning-curve":
        files.extend([curve_root / "learning-curve.json", curve_root / "execution.json"])
    files.extend(p for p in args.model_store.rglob("*") if p.is_file())
    lock = {
        "dataset_sha256": sha(args.dataset),
        "model_store": str(args.model_store.resolve()),
        "temperature": temperature,
        "frozen_at_unix": time.time(),
        "files": [{"path": str(p.resolve()), "sha256": sha(p)} for p in files],
        "promotion_eligible": False,
    }
    with args.candidate_lock.open("x", encoding="utf-8") as stream:
        json.dump(lock, stream, indent=2)
    print(json.dumps({"candidate_frozen": True, "temperature": temperature}))


def runner_choice(raw, question):
    """Decode the existing runner's uncalibrated choice logits in supplied label order."""
    if question.get("type") != "choice" or raw.get("state_truncated"):
        raise ValueError("Runner requires an untruncated choice question")
    answers = raw.get("questions", [])
    labels = list(question["criteria"])
    if len(answers) != 1 or answers[0].get("act_logits") is not None:
        raise ValueError("Unexpected runner question/act output")
    logits = answers[0].get("logits", [])
    if len(logits) != len(labels) or any(not math.isfinite(v) for v in logits):
        raise ValueError("Runner logits/order/count changed")
    values = [math.exp(v - max(logits)) for v in logits]
    probabilities = dict(zip(labels, (v / sum(values) for v in values), strict=True))
    return {"choice": max(probabilities, key=probabilities.get), "raw_distribution": probabilities}


@contextlib.contextmanager
def laya_runner(model_store, model, output):
    """Launch this installed Ollaya's own CPU runner with an explicit ORT thread count."""
    model_store = Path(model_store).resolve()
    manifest = model_store / "manifests/ollaya.dev/library" / model.replace(":", "/")
    if not manifest.resolve().is_relative_to(model_store):
        raise ValueError("Runner manifest escapes the model store")
    data = read(manifest)
    layers = {}
    for layer in [data["config"], *data["layers"]]:
        file = model_store / "blobs" / layer["digest"].replace(":", "-")
        if (
            not file.resolve().is_relative_to(model_store)
            or sha(file) != layer["digest"].split(":")[1]
        ):
            raise ValueError("Runner model blob integrity failed")
        if layer.get("annotations", {}).get("org.ollaya.precision") == "fp16":
            continue
        layers[layer["mediaType"].removeprefix("application/vnd.ollaya.")] = file
    decision = read(layers["decision"])
    calibration = read(layers["calibration"])
    if (
        decision.get("family") != "laya"
        or calibration.get("temperature") != [1.0, 1.0, 1.0]
        or calibration.get("temperature_by_options")
    ):
        raise ValueError("Direct runner only supports Laya with unit model calibration")
    binary = ROOT / ".local/ollaya-evaluation/runtime/bin/ollaya.exe"
    command = [
        str(binary),
        "runner",
        "--graph-fp32",
        str(layers["graph.onnx"]),
        "--tokenizer",
        str(layers["tokenizer"]),
        "--decision",
        str(layers["decision"]),
        "--device",
        "cpu",
        "--threads",
        "4",
    ]
    with (Path(output) / "runner-stderr.log").open("x", encoding="utf-8") as errors:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        hello_queue = queue.Queue()
        reader = threading.Thread(
            target=lambda: hello_queue.put(process.stdout.readline()), daemon=True
        )
        reader.start()
        try:
            from scripts.train_laya_pilot import memory_gib

            began = time.monotonic()
            while reader.is_alive():
                if time.monotonic() - began > 300 or memory_gib() < 1.5:
                    raise RuntimeError("Runner preparation time/memory limit")
                reader.join(0.25)
            hello = json.loads(hello_queue.get_nowait())
            if process.poll() is not None or any(
                hello.get(k) != v
                for k, v in {"device": "cpu", "precision": "fp32", "engine": "onnx"}.items()
            ):
                raise ValueError("Runner CPU/FP32/ONNX identity mismatch")
            client = Client(f"http://127.0.0.1:{int(hello['port'])}")
            health = client.call("/health")
            if any(health.get(k) != hello[k] for k in ("device", "precision", "engine")):
                raise ValueError("Runner health identity mismatch")
            yield (
                client,
                process.pid,
                {
                    "command": command,
                    "binary_sha256": sha(binary),
                    "hello": hello,
                    "health": health,
                    "intra_op_threads": 4,
                    "mode": "existing_ollaya_runner",
                },
            )
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=30)
            process.stdout.close()
            reader.join(timeout=1)


def frozen_evaluation(args):
    from jev_context.storage import FileLock
    from scripts.prepare_laya_training_data import digest, load_frozen

    if (
        not args.dataset
        or not args.manifest
        or args.data_profile == "public-pilot"
        or not args.split
    ):
        raise ValueError("Frozen evaluation needs explicit dataset/manifest/profile/split")
    cases = load_frozen(args.dataset, args.manifest, args.split, args.data_profile)
    direct_runner = getattr(args, "runner_threads", None) is not None
    if direct_runner and (
        args.runner_threads != 4
        or args.backend != "ollaya"
        or args.data_profile != "synthetic-learning-curve"
        or any(c["question"].get("type") != "choice" for c in cases)
    ):
        raise ValueError("Explicit runner threads require curve Laya choice evaluation")
    curve_budget = None
    if args.data_profile == "synthetic-learning-curve":
        from scripts.train_laya_pilot import memory_gib

        curve_budget = read(args.dataset.parent.parent / "execution.json")
        if time.time() >= curve_budget["started_at_unix"] + 43200:
            raise RuntimeError("Curve whole execution budget exhausted")
    if args.split == "test":
        if not args.candidate_lock:
            raise ValueError("Every final test requires a frozen candidate")
        check_candidate_lock(
            args.candidate_lock,
            args.dataset,
            args.model_store if args.backend == "ollaya" and args.model != BASE_MODEL else None,
        )
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / (args.split + ".json")
    with target.open("x", encoding="utf-8") as stream:
        json.dump({"status": "started", "split": args.split}, stream)
    report = {
        "status": "running",
        "split": args.split,
        "data_profile": args.data_profile,
        "dataset_sha256": sha(args.dataset),
        "manifest_sha256": sha(args.manifest),
        "backend": args.backend,
        "model": args.model if args.backend == "ollaya" else "product-semif",
        "rows": [],
        "memory_samples": [],
        "promotion_eligible": False,
        "test_results_sealed": args.split == "test",
        "candidate_lock_sha256": sha(args.candidate_lock) if args.candidate_lock else None,
        "started_at_unix": time.time(),
        "conditions": {
            "repeats": 3 if args.split == "test" else 1,
            "case_order": [c["id"] for c in cases],
            "memory_sample_interval_seconds": 5,
            "memory_kind": "observed process-tree working set; not GPU memory",
            "latency": "all repeated requests; model load separately recorded",
            "input_limit": 1024,
        },
    }
    client = Client(args.endpoint)
    engine = None
    owned_ollaya_load = False
    stopped = threading.Event()
    monitor = None
    profile = read(args.profile)
    leases = contextlib.ExitStack()
    try:
        leases.enter_context(FileLock(ROOT / ".local/laya-finetuning/experiment-model.lock"))
        if args.backend == "ollaya":
            leases.enter_context(FileLock(Path(profile["lock_root"]) / "resident.lock"))
        if not direct_runner and client.call("/api/ps")["models"]:
            raise RuntimeError("Other Ollaya model is resident")
        began = time.perf_counter()
        if curve_budget and memory_gib() < 4:
            raise MemoryError("Require 4 GiB before evaluation model load")
        if args.backend == "semif":
            engine = SemifOpenVINO(profile)
            engine.prepare()
            report.update(profile=profile, fingerprint=engine.fingerprint, startup=engine.startup)
            pid = engine.process.pid
        else:
            if not args.server_pid and not direct_runner:
                raise ValueError("Ollaya needs owned server PID")
            manifest = (
                args.model_store / "manifests/ollaya.dev/library" / args.model.replace(":", "/")
            )
            report["model_manifest_sha256"] = sha(manifest)
            manifest_data = read(manifest)
            for layer in [manifest_data["config"], *manifest_data["layers"]]:
                blob = args.model_store / "blobs" / layer["digest"].replace(":", "-")
                if sha(blob) != layer["digest"].split(":")[1]:
                    raise ValueError("Model blob integrity failed")
            report["model_config"] = read(
                args.model_store / "blobs" / manifest_data["config"]["digest"].replace(":", "-")
            )
            if direct_runner:
                client, pid, report["runner"] = leases.enter_context(
                    laya_runner(args.model_store, args.model, args.output)
                )
                report["conditions"]["intra_op_threads"] = 4
                report["conditions"]["execution_mode"] = "existing_ollaya_runner"
            else:
                report["runtime_version"] = client.call("/api/version")
                owned_ollaya_load = True
                report["load"] = client.call(
                    "/api/decide", {"model": args.model, "keep_alive": -1}, timeout=300
                )
                pid = args.server_pid
        report["preparation_seconds"] = time.perf_counter() - began

        def sample():
            while not stopped.is_set():
                try:
                    report["memory_samples"].append(process_snapshot(pid))
                except Exception as exc:
                    report["memory_samples"].append({"error": str(exc)})
                stopped.wait(5)

        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        failures = 0
        for repeat in range(report["conditions"]["repeats"]):
            for case in cases:
                row = {
                    k: case[k]
                    for k in (
                        "id",
                        "split",
                        "purpose",
                        "expected",
                        "critical",
                        "group_id",
                        "state_sha256",
                    )
                }
                row.update(repeat=repeat, question_sha256=digest(case["question"]))
                start = time.perf_counter()
                if failures >= 3:
                    row.update(status="not_run", reason="three_consecutive_errors")
                else:
                    try:
                        if curve_budget and (
                            time.time() >= curve_budget["started_at_unix"] + 43200
                            or time.perf_counter() - began > 5400
                            or memory_gib() < 1.5
                        ):
                            raise RuntimeError("Curve evaluation time or memory limit")
                        q = question_contract(case["purpose"], case["question"])
                        if engine:
                            answer = engine.evaluate(
                                {
                                    "evaluation_id": f"{case['id']}-{repeat}",
                                    "profile_fingerprint": engine.fingerprint,
                                    "deadline_ms": 30000,
                                    "state": case["state"],
                                    "questions": [q],
                                }
                            )["answers"][0]
                        elif direct_runner:
                            raw = client.call(
                                "/decide",
                                {
                                    "state": case["state"],
                                    "questions": {case["purpose"]: case["question"]},
                                },
                                timeout=30,
                            )
                            answer = runner_choice(raw, case["question"])
                        else:
                            raw = client.call(
                                "/v1/systemone",
                                {
                                    "model": args.model,
                                    "state": case["state"],
                                    "questions": {case["purpose"]: case["question"]},
                                },
                                timeout=30,
                            )
                            if raw.get("state_truncated") or raw.get("model") != args.model:
                                raise ValueError("Truncation or model identity mismatch")
                            answer = convert(raw, [q], "Ollaya probability")[0]
                        probabilities = answer["raw_distribution"]
                        if set(probabilities) != set(case["question"]["criteria"]) or any(
                            not math.isfinite(v) for v in probabilities.values()
                        ):
                            raise ValueError("Incomplete or nonfinite distribution")
                        row.update(
                            status="observed",
                            choice=answer["choice"],
                            probabilities=probabilities,
                        )
                        failures = 0
                    except Exception as exc:
                        row.update(status="error", reason=str(exc))
                        failures += 1
                    row["latency_ms"] = (time.perf_counter() - start) * 1000
                report["rows"].append(row)
                save(target, report)
            print(
                f"{args.backend} {args.split} repeat {repeat + 1} recorded (predictions hidden)",
                flush=True,
            )
        report["status"] = (
            "completed" if all(r["status"] == "observed" for r in report["rows"]) else "incomplete"
        )
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        # Preserve every not-run case, even when preparation failed.
        done = {(r["repeat"], r["id"]) for r in report["rows"]}
        for repeat in range(report["conditions"]["repeats"]):
            for case in cases:
                if (repeat, case["id"]) not in done:
                    report["rows"].append(
                        {
                            **{
                                k: case[k]
                                for k in (
                                    "id",
                                    "split",
                                    "purpose",
                                    "expected",
                                    "critical",
                                    "group_id",
                                    "state_sha256",
                                )
                            },
                            "repeat": repeat,
                            "question_sha256": digest(case["question"]),
                            "status": "not_run",
                            "reason": "preparation_or_run_failed",
                        }
                    )
    finally:
        stopped.set()
        if monitor:
            monitor.join(timeout=35)
        if engine:
            engine.close()
        if owned_ollaya_load:
            try:
                client.unload(args.model)
            except Exception as exc:
                report.update(status="failed", unload_error=str(exc))
        leases.close()
        report["finished_at_unix"] = time.time()
        save(target, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "recorded": len(report["rows"]),
                "sealed": args.split == "test",
            }
        )
    )
    if report["status"] != "completed":
        raise SystemExit(1)


def paired_comparison(baseline, candidate):
    if (
        baseline.get("data_profile") == "synthetic-learning-curve"
        and baseline.get("backend") == candidate.get("backend") == "ollaya"
    ):
        for report in (baseline, candidate):
            if (
                report["conditions"].get("intra_op_threads") != 4
                or report["conditions"].get("execution_mode") != "existing_ollaya_runner"
                or report.get("runner", {}).get("hello", {}).get("device") != "cpu"
                or report.get("runner", {}).get("hello", {}).get("precision") != "fp32"
            ):
                raise ValueError("Curve comparison requires matching CPU/FP32/four-thread runners")
        if baseline["runner"]["binary_sha256"] != candidate["runner"]["binary_sha256"]:
            raise ValueError("Curve runner binary changed between models")
    if (
        baseline["dataset_sha256"] != candidate["dataset_sha256"]
        or baseline["conditions"]["case_order"] != candidate["conditions"]["case_order"]
    ):
        raise ValueError("Cannot compare different datasets or case orders")
    left = {(r["repeat"], r["id"]): r for r in baseline["rows"]}
    right = {(r["repeat"], r["id"]): r for r in candidate["rows"]}
    if (
        set(left) != set(right)
        or len(left) != len(baseline["rows"])
        or len(right) != len(candidate["rows"])
    ):
        raise ValueError("Missing or duplicate evaluation rows")
    for key in left:
        if any(
            left[key][k] != right[key][k]
            for k in (
                "state_sha256",
                "question_sha256",
                "expected",
                "group_id",
                "split",
                "purpose",
                "critical",
            )
        ):
            raise ValueError("Comparison input, label, question or group mismatch")
    result = {}
    for purpose in ("relevance", "evidence_relation", "capability_fit"):
        a = [r for r in left.values() if r["purpose"] == purpose and r["repeat"] == 0]
        b = [right[0, r["id"]] for r in a]
        groups = sorted({r["group_id"] for r in a})
        changes = [
            {
                "id": x["id"],
                "group_id": x["group_id"],
                "before_correct": x.get("choice") == x["expected"],
                "after_correct": y.get("choice") == y["expected"],
            }
            for x, y in zip(a, b, strict=True)
        ]

        def delta(c):
            return sum(int(x["after_correct"]) - int(x["before_correct"]) for x in c) / len(c)

        rng = random.Random(20260928)
        samples = sorted(
            delta(
                [
                    x
                    for g in rng.choices(groups, k=len(groups))
                    for x in changes
                    if x["group_id"] == g
                ]
            )
            for _ in range(10000)
        )
        ma, mb = metrics(a), metrics(b)
        repeated_a = metrics([r for r in left.values() if r["purpose"] == purpose])
        repeated_b = metrics([r for r in right.values() if r["purpose"] == purpose])

        def memory(report):
            values = [
                s["working_set_sum_bytes"]
                for s in report["memory_samples"]
                if s.get("working_set_sum_bytes", 0) > 0
            ]
            return max(values) if values else None

        ram_a, ram_b = memory(baseline), memory(candidate)
        latency_ok = bool(
            repeated_a["p95_ms"]
            and repeated_b["p95_ms"]
            and repeated_b["p95_ms"] < repeated_a["p95_ms"] * 1.1
        )
        passed = bool(
            samples[250] > 0
            and mb["critical_errors"] <= ma["critical_errors"]
            and latency_ok
            and ram_a
            and ram_b
            and ram_b < ram_a * 1.1
            and baseline["status"] == candidate["status"] == "completed"
        )
        result[purpose] = {
            "baseline": ma,
            "candidate": mb,
            "difference": delta(changes),
            "bootstrap_95": [samples[250], samples[9749]],
            "groups": len(groups),
            "repeated_baseline": repeated_a,
            "repeated_candidate": repeated_b,
            "memory_bytes": [ram_a, ram_b],
            "changes": [c for c in changes if c["before_correct"] != c["after_correct"]],
            "improvement_gate_passed": passed,
        }
    return result


def compare_frozen(args):
    from scripts.prepare_laya_training_data import load_frozen

    cases = load_frozen(args.dataset, args.manifest, "test", args.data_profile)
    lock = check_candidate_lock(args.candidate_lock, args.dataset)
    baseline, candidate, product = (
        read(p) for p in (args.baseline_report, args.candidate_report, args.product_report)
    )
    for report in (baseline, candidate, product):
        validate_frozen_evaluation_rows(report, cases, "test", 3)
        if (
            report["split"] != "test"
            or report["dataset_sha256"] != sha(args.dataset)
            or report["manifest_sha256"] != sha(args.manifest)
        ):
            raise ValueError("Comparison requires the same frozen test")
        if (
            report["conditions"]["case_order"] != [c["id"] for c in cases]
            or len(report["rows"]) != len(cases) * 3
        ):
            raise ValueError("Comparison requires all cases and three repeats")
    if candidate["started_at_unix"] < lock["frozen_at_unix"] or candidate.get(
        "candidate_lock_sha256"
    ) != sha(args.candidate_lock):
        raise ValueError("Candidate test must follow the exact candidate lock")
    # Mark consumed before reading/analyzing predictions; an interrupted analysis cannot reset it.
    ledger = args.dataset.parent / "test-consumed.json"
    with ledger.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "dataset_sha256": sha(args.dataset),
                "candidate_lock_sha256": sha(args.candidate_lock),
                "consumed_at_unix": time.time(),
                "reason": "Final predictions opened; any candidate revision requires a new test",
            },
            stream,
            indent=2,
        )
    paired = paired_comparison(baseline, candidate)
    product_paired = paired_comparison(product, candidate)
    baseline_cal = read(args.baseline_report.with_name("calibration.json"))
    if (
        baseline_cal["dataset_sha256"] != sha(args.dataset)
        or baseline_cal["model_manifest_sha256"] != baseline["model_manifest_sha256"]
    ):
        raise ValueError("Baseline calibration mismatch")
    temperatures = {"baseline": choose_temperature(baseline_cal), "candidate": lock["temperature"]}
    calibrated = {}
    for name, report in (("baseline", baseline), ("candidate", candidate)):
        rows = copy.deepcopy([r for r in report["rows"] if r["repeat"] == 0])
        for row in rows:
            if row.get("probabilities"):
                row["probabilities"] = scale(row["probabilities"], temperatures[name])
        calibrated[name] = metrics(rows)
    result = {
        "data_profile": args.data_profile,
        "dataset_sha256": sha(args.dataset),
        "candidate_lock_sha256": sha(args.candidate_lock),
        "paired_laya": paired,
        "product_comparison": product_paired,
        "temperatures": temperatures,
        "calibrated_probability_metrics": calibrated,
        "conclusion": "synthetic_improvement_observed"
        if all(p["improvement_gate_passed"] for p in paired.values())
        else "improvement_unconfirmed",
        "actual_business_improvement": "unverified",
        "promotion_eligible": False,
        "reports": {
            name: {
                "sha256": sha(path),
                "preparation_seconds": r.get("preparation_seconds"),
                "conditions": r["conditions"],
            }
            for name, path, r in (
                ("baseline", args.baseline_report, baseline),
                ("candidate", args.candidate_report, candidate),
                ("product", args.product_report, product),
            )
        },
        "limitations": [
            "Same agent authored and reviewed labels; synthetic scenarios only",
            "Situation groups are the sampling unit; correlated repeats are not independent samples",
            "Product GPU versus Laya CPU; process working set excludes GPU allocation",
        ],
    }
    result["unstable_cases"] = {
        name: sum(
            len({r.get("choice", r["status"]) for r in report["rows"] if r["id"] == case["id"]}) > 1
            for case in cases
        )
        for name, report in (("baseline", baseline), ("candidate", candidate), ("product", product))
    }
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "comparison.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "conclusion": result["conclusion"],
                "laya": {
                    p: {
                        "before": r["baseline"]["correct"],
                        "after": r["candidate"]["correct"],
                        "count": r["candidate"]["count"],
                        "interval": r["bootstrap_95"],
                        "passed": r["improvement_gate_passed"],
                    }
                    for p, r in paired.items()
                },
            },
            ensure_ascii=False,
        )
    )


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process_snapshot(root_pid):
    """Windows working-set sample of this owned process tree, not a peak or GPU measurement."""
    root_pid = int(root_pid)

    # WMI can return an empty array with exit code zero in restricted sessions.
    # Toolhelp/PSAPI reads only PID ancestry and memory, with no command-line or file reads.
    class Entry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD),
            ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD),
            ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
            ("name", wintypes.WCHAR * 260),
        ]

    class Counters(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak_ws",
                "working_set",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Entry),
    ]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    entries = []
    entry = Entry()
    entry.size = ctypes.sizeof(entry)
    try:
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            entries.append((entry.pid, entry.parent, entry.name))
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    ids = {root_pid}
    while True:
        next_ids = ids | {pid for pid, parent, _ in entries if parent in ids}
        if next_ids == ids:
            break
        ids = next_ids
    rows = []
    for pid, parent, name in entries:
        if pid not in ids:
            continue
        handle = kernel.OpenProcess(0x1000 | 0x10, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        counters = Counters()
        counters.size = ctypes.sizeof(counters)
        try:
            if not psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), ctypes.sizeof(counters)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)
        rows.append(
            {
                "ProcessId": pid,
                "ParentProcessId": parent,
                "Name": name,
                "WorkingSetSize": counters.working_set,
            }
        )
    if (
        not rows
        or root_pid not in {r["ProcessId"] for r in rows}
        or any(r["WorkingSetSize"] <= 0 for r in rows)
    ):
        raise RuntimeError("Owned process memory unavailable; must not report zero")
    return {
        "method": "Windows Toolhelp32 + PSAPI",
        "processes": rows,
        "working_set_sum_bytes": sum(r["WorkingSetSize"] for r in rows),
    }


def fixtures():
    frozen = read(FIXTURES / "frozen.json")
    for name, expected in frozen["files"].items():
        if sha(FIXTURES / name) != expected:
            raise ValueError(f"Frozen fixture changed: {name}")
    return read(FIXTURES / "cases.json")["cases"], read(FIXTURES / "questions.json"), frozen


class Client:
    def __init__(self, endpoint):
        address = urlsplit(endpoint)
        if (
            address.scheme != "http"
            or not ipaddress.ip_address(address.hostname).is_loopback
            or address.username
            or address.password
            or address.path not in ("", "/")
            or address.query
            or address.fragment
        ):
            raise ValueError("A literal HTTP loopback endpoint is required")
        self.host, self.port = address.hostname, address.port or 80

    def call(self, path, body=None, timeout=10):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            connection.request(
                "GET" if body is None else "POST",
                path,
                None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            value = json.loads(response.read())
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}: {json.dumps(value)}")
            return value
        finally:
            connection.close()

    def unload(self, model):
        result = self.call("/api/decide", {"model": model, "keep_alive": 0}, timeout=60)
        if result.get("done_reason") != "unload" or self.call("/api/ps")["models"]:
            raise RuntimeError("Model unload was not confirmed")
        return result


def scale(probabilities, temperature):
    if temperature <= 0 or not math.isfinite(temperature):
        raise ValueError("Temperature must be finite and positive")
    values = {key: max(value, 1e-8) ** (1 / temperature) for key, value in probabilities.items()}
    total = sum(values.values())
    return {key: value / total for key, value in values.items()}


def metrics(rows, threshold=0):
    observed = [r for r in rows if r.get("probabilities")]
    accepted = [
        r
        for r in observed
        if r["choice"] != "insufficient_evidence" and max(r["probabilities"].values()) >= threshold
    ]
    latency = sorted(r["latency_ms"] for r in observed)
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
        "nll_observed": statistics.mean(
            -math.log(max(r["probabilities"][r["expected"]], 1e-8)) for r in observed
        )
        if observed
        else None,
        "brier_observed": statistics.mean(
            sum((p - int(k == r["expected"])) ** 2 for k, p in r["probabilities"].items())
            for r in observed
        )
        if observed
        else None,
        "p50_ms": statistics.median(latency) if latency else None,
        "p95_ms": latency[math.ceil(len(latency) * 0.95) - 1] if latency else None,
        "within_2s": sum(r["latency_ms"] <= 2000 for r in observed),
        "within_6s": sum(r["latency_ms"] <= 6000 for r in observed),
        "confusion": dict(
            Counter(f"{r['purpose']}:{r['expected']}->{r.get('choice', r['status'])}" for r in rows)
        ),
    }


def select(development):
    # Only development rows enter selection; fail rather than fit a failed/incomplete run.
    if any(
        r["split"] != "development" or r["status"] != "observed"
        for rows in development.values()
        for r in rows
    ):
        raise ValueError("Selection needs complete development observations only")
    variant = max(
        development,
        key=lambda v: (
            metrics(development[v])["correct"],
            -metrics(development[v])["critical_errors"],
            v == "baseline",
        ),
    )
    rows = development[variant]
    candidates = []
    for temperature in TEMPERATURES:
        calibrated = [{**r, "probabilities": scale(r["probabilities"], temperature)} for r in rows]
        candidates.append((metrics(calibrated)["nll_observed"], temperature, calibrated))
    _, temperature, calibrated = min(candidates, key=lambda item: (item[0], abs(item[1] - 1)))
    threshold = 1.01  # Explicit accept-none result.
    for candidate in THRESHOLDS:
        report = metrics(calibrated, candidate)
        if (
            report["accepted"] >= 10
            and report["accepted_correct"] / report["accepted"] >= 0.9
            and report["critical_wrong_accepts"] == 0
        ):
            threshold = candidate
            break
    return {
        "variant": variant,
        "relative_temperature": temperature,
        "threshold": threshold,
        "development_metrics": metrics(calibrated, threshold),
    }


def question_contract(purpose, wire):
    return {
        "id": purpose,
        "purpose": purpose,
        "language": "ko",
        "instructions": wire["instructions"],
        "options": wire["criteria"],
    }


def run_rows(cases, evaluator, checkpoint):
    rows, failures = [], 0
    for case in cases:
        row = {key: case[key] for key in ("id", "split", "purpose", "expected", "critical")}
        if failures >= 3:
            row.update(status="not_run", reason="three_consecutive_errors")
        else:
            started = time.perf_counter()
            try:
                answer = evaluator(case)
                row.update(
                    status="observed",
                    choice=answer["choice"],
                    probabilities=answer["raw_distribution"],
                    raw_confidence=answer["raw_confidence"],
                )
                failures = 0
            except Exception as exc:
                row.update(status="error", reason=str(exc))
                failures += 1
            row["latency_ms"] = (time.perf_counter() - started) * 1000
        rows.append(row)
        save(checkpoint, rows)
        print(f"{checkpoint.stem} {case['id']} {row['status']} {row.get('choice', '')}", flush=True)
    return rows


def ollaya_rows(client, model, cases, questions, checkpoint):
    if client.call("/api/ps")["models"]:
        raise RuntimeError("Unload the previous model before this run")
    started = time.perf_counter()
    load = client.call("/api/decide", {"model": model, "keep_alive": -1}, timeout=300)
    preparation = time.perf_counter() - started

    def evaluate(case):
        purpose = case["purpose"]
        raw = client.call(
            "/v1/systemone",
            {"model": model, "state": case["state"], "questions": {purpose: questions[purpose]}},
        )
        if raw.get("state_truncated"):
            raise ValueError("Truncated state")
        if raw.get("model") != model:
            raise ValueError("Unexpected model identity")
        return convert(
            raw,
            [question_contract(purpose, questions[purpose])],
            "Ollaya normalized maximum probability",
        )[0]

    try:
        rows = run_rows(cases, evaluate, checkpoint)
        residency = client.call("/api/ps")
    finally:
        client.unload(model)
    return {
        "preparation_seconds": preparation,
        "load": load,
        "residency": residency,
        "first_request_ms": rows[0].get("latency_ms"),
        "metrics": metrics(rows),
        "rows": rows,
    }


def identity(model_store):
    manifest = model_store / "manifests/ollaya.dev/library/laya/multilingual"
    value = read(manifest)
    calibration = None
    hashes = {"manifest": sha(manifest)}
    for layer in [value["config"], *value["layers"]]:
        blob = model_store / "blobs" / layer["digest"].replace(":", "-")
        actual = sha(blob)
        if actual != layer["digest"].split(":")[1] or blob.stat().st_size != layer["size"]:
            raise ValueError(f"Model integrity mismatch: {blob.name}")
        hashes[blob.name] = actual
        if layer["mediaType"] == "application/vnd.ollaya.calibration":
            calibration = read(blob)
    if calibration is None:
        raise ValueError("Base calibration is unavailable")
    lock = read(FIXTURES / "model-lock.json")
    if hashes != lock["hashes"]:
        raise ValueError("Registry model changed since the recorded experiment")
    return {"hashes": hashes, "calibration": calibration}


def probe_digest(value):
    # Dictionary insertion order is part of the choice presentation contract.
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def load_probes(path):
    payload = read(path)
    cases = payload["cases"]
    if payload.get("usage") != "development_probe" or payload.get("sha256") != probe_digest(cases):
        raise ValueError("Probe usage or content hash changed")
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Probe IDs must be unique")
    for c in cases:
        if c["split"] != "development" or c["usage"] not in {
            "development_probe",
            "external_reproduction",
        }:
            raise ValueError("Probes are development only")
        if c["question"].get("type") != "choice" or c["expected"] not in c["question"]["criteria"]:
            raise ValueError("Probe label/question mismatch")
        if c["state_sha256"] != probe_digest(c["state"]) or c["question_sha256"] != probe_digest(
            c["question"]
        ):
            raise ValueError("Probe input/question hash changed")
        if not c.get("source") or not c.get("rationale"):
            raise ValueError("Probe source and review required")
    by_id = {c["id"]: c for c in cases}
    for c in cases:
        if c.get("reversed_from"):
            original = by_id[c["reversed_from"]]
            expected_q = copy.deepcopy(original["question"])
            expected_q["criteria"] = dict(reversed(list(expected_q["criteria"].items())))
            if (
                c["question"] != expected_q
                or list(c["question"]["criteria"]) != list(expected_q["criteria"])
                or c["state"] != original["state"]
                or c["expected"] != original["expected"]
            ):
                raise ValueError("Reverse probe changed meaning or label order")
    return cases


def compare_probes(reference, actual):
    fields = ("id", "purpose", "expected", "state_sha256", "question_sha256", "labels")
    if reference["dataset_sha256"] != actual["dataset_sha256"] or len(reference["rows"]) != len(
        actual["rows"]
    ):
        raise ValueError("Probe dataset or row count mismatch")
    rows = []
    for left, right in zip(reference["rows"], actual["rows"], strict=True):
        if any(left[k] != right[k] for k in fields):
            raise ValueError("Probe input/order mismatch")
        row = {"id": left["id"], "choice_match": False, "max_probability_error": None}
        if left.get("status") == right.get("status") == "observed":
            row["choice_match"] = left["choice"] == right["choice"]
            row["max_probability_error"] = max(
                abs(left["probabilities"][k] - right["probabilities"][k]) for k in left["labels"]
            )
        rows.append(row)
    return {
        "rows": rows,
        "count": len(rows),
        "passed": bool(rows)
        and all(r["choice_match"] and r["max_probability_error"] <= 0.005 for r in rows),
    }


def complete_probe_failures(report, cases, reason):
    by_id = {r["id"]: r for r in report["rows"]}
    for c in cases:
        if c["id"] not in by_id:
            row = {
                k: c[k] for k in ("id", "purpose", "expected", "state_sha256", "question_sha256")
            }
            row.update(labels=list(c["question"]["criteria"]), status="not_run", reason=reason)
            report["rows"].append(row)
        elif "status" not in by_id[c["id"]]:
            by_id[c["id"]].update(status="error", reason=reason)


def probe_evaluation(args):
    import os

    from jev_context.storage import FileLock
    from scripts.train_laya_pilot import memory_gib

    if not args.probe_dataset or any(
        (args.dataset, args.manifest, args.split, args.candidate_lock)
    ):
        raise ValueError("Probe input cannot be combined with frozen inputs")
    if args.data_profile != "synthetic-experiment" or args.backend not in {"python", "ollaya"}:
        raise ValueError("Probe needs synthetic-experiment and Python/Ollaya")
    cases = load_probes(args.probe_dataset)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "usage": "development_probe",
        "quality_evaluation": False,
        "dataset_sha256": sha(args.probe_dataset),
        "code_sha256": sha(Path(__file__)),
        "backend": args.backend,
        "model": args.model,
        "rows": [],
    }
    initial = copy.deepcopy(report)
    initial["status"] = "started"
    complete_probe_failures(initial, cases, "not_started")
    save(args.output / "probe.json", initial)
    stopped = threading.Event()
    began = time.perf_counter()
    deadline = {"case": None}

    def watchdog():
        while not stopped.wait(0.5):
            if (
                time.perf_counter() - began > 1200
                or memory_gib() < 1.5
                or (deadline["case"] and time.perf_counter() > deadline["case"])
            ):
                report.update(status="failed", reason="time_or_memory_limit")
                complete_probe_failures(report, cases, "time_or_memory_limit")
                save(args.output / "probe.json", report)
                os._exit(124)

    client = Client(args.endpoint)
    with (
        FileLock(ROOT / ".local/laya-finetuning/experiment-model.lock"),
        FileLock(Path(read(args.profile)["lock_root"]) / "resident.lock"),
    ):
        if client.call("/api/ps")["models"]:
            raise ValueError("Another model is resident")
        if memory_gib() < 4:
            raise MemoryError("Require 4 GiB before loading")
        threading.Thread(target=watchdog, daemon=True).start()
        try:
            if args.backend == "python":
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                import torch
                from laya import Agent
                from laya.common import render_options, serialize_state

                torch.set_num_threads(4)
                agent = Agent(str(args.python_model), device="cpu")
                agent.model.eval()
                report["checkpoint_sha256"] = sha(args.python_model / "model.safetensors")
            else:
                report["identity"] = identity(args.model_store)
                report["version"] = client.call("/api/version")
                client.call("/api/decide", {"model": args.model, "keep_alive": -1}, timeout=300)
            report["preparation_seconds"] = time.perf_counter() - began
            for c in cases:
                row = {
                    k: c[k]
                    for k in ("id", "purpose", "expected", "state_sha256", "question_sha256")
                }
                row["labels"] = list(c["question"]["criteria"])
                report["rows"].append(row)
                started = time.perf_counter()
                deadline["case"] = started + 30
                try:
                    if args.backend == "python":
                        q = agent._to_internal(c["question"])
                        state = serialize_state(c["state"])
                        options = render_options(q)
                        lengths = [
                            len(agent.tok(" " + o, add_special_tokens=False)["input_ids"])
                            for o in options
                        ]
                        head = len(
                            agent.tok(f"{q['t']} question: {q['ins']}", add_special_tokens=False)[
                                "input_ids"
                            ]
                        )
                        state_len = len(agent.tok(state, add_special_tokens=False)["input_ids"])
                        if (
                            any(agent.tok.mask_token in t for t in [state, q["ins"], *options])
                            or any(n > 48 for n in lengths)
                            or max(head, 16) + sum(lengths) + len(lengths) > 256
                            or head + sum(lengths) + len(lengths) + state_len + 4 > 1024
                        ):
                            raise ValueError("Input would be truncated or rewritten")
                        raw = agent.system_one(c["state"], {c["purpose"]: c["question"]})
                    else:
                        raw = client.call(
                            "/v1/systemone",
                            {
                                "model": args.model,
                                "state": c["state"],
                                "questions": {c["purpose"]: c["question"]},
                            },
                            timeout=30,
                        )
                        if raw.get("model") != args.model:
                            raise ValueError("Unexpected model")
                    if raw.get("state_truncated"):
                        raise ValueError("Input was truncated")
                    answer = raw["answers"][c["purpose"]]
                    probs = answer["probabilities"]
                    if (
                        set(probs) != set(row["labels"])
                        or answer["choice"] not in probs
                        or not all(math.isfinite(p) and 0 <= p <= 1 for p in probs.values())
                    ):
                        raise ValueError("Invalid probabilities or labels")
                    row.update(status="observed", choice=answer["choice"], probabilities=probs)
                except Exception as exc:
                    row.update(status="error", reason=str(exc))
                finally:
                    deadline["case"] = None
                    row["latency_ms"] = (time.perf_counter() - started) * 1000
                    save(args.output / "probe.json", report)
                print(c["id"], row["status"], flush=True)
            report["status"] = (
                "completed" if all(r["status"] == "observed" for r in report["rows"]) else "failed"
            )
        except Exception as exc:
            report.update(status="failed", reason=str(exc))
            raise
        finally:
            stopped.set()
            complete_probe_failures(report, cases, report.get("reason", "interrupted"))
            try:
                if args.backend == "ollaya":
                    client.unload(args.model)
                    report["after_unload"] = client.call("/api/ps")
            finally:
                save(args.output / "probe.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "probe",
            "develop",
            "validate",
            "semif",
            "contracts",
            "resources",
            "frozen",
            "freeze-candidate",
            "compare",
            "curve-summary",
        ],
    )
    parser.add_argument("--probe-dataset", type=Path)
    parser.add_argument("--curve-root", type=Path)
    parser.add_argument(
        "--python-model", type=Path, default=ROOT / ".local/models/laya-multilingual"
    )
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--split", choices=["train", "development", "calibration", "test"])
    parser.add_argument(
        "--data-profile",
        choices=["actual", "synthetic-experiment", "synthetic-learning-curve", "public-pilot"],
        required=True,
    )
    parser.add_argument("--backend", choices=["ollaya", "semif", "python"], default="ollaya")
    parser.add_argument("--model", default=BASE_MODEL)
    parser.add_argument("--candidate-lock", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--calibration-report", type=Path)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--candidate-report", type=Path)
    parser.add_argument("--product-report", type=Path)
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--runner-threads", type=int, choices=[4])
    parser.add_argument("--endpoint", default="http://127.0.0.1:11437")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/ollaya-evaluation/results")
    parser.add_argument(
        "--model-store", type=Path, default=ROOT / ".local/ollaya-evaluation/models"
    )
    parser.add_argument("--profile", type=Path, default=ROOT / ".local/semif-ov-profile.json")
    args = parser.parse_args()
    if args.stage == "curve-summary":
        if args.data_profile != "synthetic-learning-curve" or not args.curve_root:
            parser.error("curve-summary requires --curve-root and synthetic-learning-curve")
        report = summarize_learning_curve(args.curve_root)
        args.output.mkdir(parents=True, exist_ok=True)
        save(args.output / "learning-curve.json", report)
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in ("status", "conclusion", "selected_setting", "selected_seed")
                },
                ensure_ascii=False,
            )
        )
        return
    if args.stage == "probe":
        existed = args.output.exists()
        try:
            probe_evaluation(args)
        except Exception as exc:
            target = args.output / "probe.json"
            if not existed and target.exists():
                report = read(target)
                report.update(status="failed", reason=str(exc))
                complete_probe_failures(report, load_probes(args.probe_dataset), str(exc))
                save(target, report)
            raise
        return
    if args.probe_dataset or args.backend == "python":
        parser.error("Probe dataset and Python backend require probe stage")
    if args.stage == "compare":
        compare_frozen(args)
        return
    if args.stage == "frozen":
        frozen_evaluation(args)
        return
    if args.stage == "freeze-candidate":
        freeze_candidate(args)
        return
    if args.data_profile != "public-pilot" or args.dataset or args.manifest:
        parser.error(
            "Legacy public diagnostics require --data-profile public-pilot without dataset/manifest"
        )
    cases, questions, frozen = fixtures()
    client = Client(args.endpoint)
    output = args.output
    stage_path = output / f"{args.stage}.json"
    if stage_path.exists():
        raise ValueError("Existing run is immutable; use a new --output directory")
    output.mkdir(parents=True, exist_ok=True)
    # Reserve even interrupted stages: a retry needs a separate output directory.
    with (output / f"{args.stage}.started").open("x", encoding="utf-8") as marker:
        marker.write(str(time.time()))
    report = {
        "stage": args.stage,
        "fixtures": frozen,
        "promotion_eligible": False,
        "dataset_provenance": "agent_authored_diagnostic",
    }
    if args.stage != "semif":
        model = identity(args.model_store)
        report["model"] = model
        report["version"] = client.call("/api/version")
        if report["version"]["version"] != read(FIXTURES / "model-lock.json")["version"]:
            raise ValueError("Ollaya binary version changed")
    if args.stage in {"develop", "validate"}:
        if args.stage == "develop":
            subset = [c for c in cases if c["split"] == "development"]
            report["runs"] = {
                variant: ollaya_rows(
                    client, BASE_MODEL, subset, wire, output / f"development-{variant}.json"
                )
                for variant, wire in questions.items()
            }
            choice = select({name: run["rows"] for name, run in report["runs"].items()})
            calibration = copy.deepcopy(model["calibration"])
            base_temperature = calibration.get("temperature_by_options", {}).get(
                "choice:3-5", calibration["temperature"][0]
            )
            calibration.setdefault("temperature_by_options", {})["choice:3-5"] = (
                base_temperature * choice["relative_temperature"]
            )
            choice.update(
                calibration=calibration,
                base_temperature=base_temperature,
                model_hashes=model["hashes"],
                fixtures=frozen,
            )
            save(output / "selection.json", choice)
            save(output / "calibration.json", calibration)
            save(output / "selected-questions.json", questions[choice["variant"]])
            (output / "Modelfile").write_text(
                "FROM laya:multilingual\nQUESTIONS ./selected-questions.json\nCALIBRATION ./calibration.json\nPARAMETER precision fp32\nDESCRIPTION Jev Korean diagnostic only; not approved for production\n",
                encoding="utf-8",
            )
            report["selection"] = choice
        else:
            choice = read(output / "selection.json")
            if choice != read(output / "develop.json")["selection"]:
                raise ValueError("Development selection changed")
            if choice["fixtures"] != frozen or choice["model_hashes"] != model["hashes"]:
                raise ValueError("Selection identity changed")
            report["selection_sha256"] = sha(output / "selection.json")
            subset = [c for c in cases if c["split"] == "validation"]
            report["runs"] = {}
            for name, variant, model_name in [
                ("baseline", "baseline", BASE_MODEL),
                ("selected", choice["variant"], BASE_MODEL),
                ("calibrated", choice["variant"], "jev-diagnostic:latest"),
            ]:
                if name == "calibrated":
                    report["create"] = client.call(
                        "/api/create",
                        {
                            "model": model_name,
                            "from": BASE_MODEL,
                            "questions": questions[variant],
                            "calibration": choice["calibration"],
                            "parameters": {"precision": "fp32"},
                            "stream": False,
                        },
                    )
                run = ollaya_rows(
                    client,
                    model_name,
                    subset,
                    questions[variant],
                    output / f"validation-{name}.json",
                )
                run["metrics_with_development_threshold"] = metrics(
                    run["rows"], choice["threshold"]
                )
                report["runs"][name] = run
            differences = []
            for original, calibrated in zip(
                report["runs"]["selected"]["rows"],
                report["runs"]["calibrated"]["rows"],
                strict=True,
            ):
                if original.get("probabilities") and calibrated.get("probabilities"):
                    predicted = scale(original["probabilities"], choice["relative_temperature"])
                    differences.append(
                        max(abs(v - calibrated["probabilities"][k]) for k, v in predicted.items())
                    )
            report["calibration_probability_max_difference"] = max(differences, default=None)
    elif args.stage == "semif":
        if client.call("/api/ps")["models"]:
            raise RuntimeError("Ollaya must be unloaded first")
        profile = read(args.profile)
        engine = SemifOpenVINO(profile)
        started = time.perf_counter()
        try:
            engine.prepare()
            report.update(
                preparation_seconds=time.perf_counter() - started,
                fingerprint=engine.fingerprint,
                startup=engine.startup,
            )

            def evaluate(case):
                return engine.evaluate(
                    {
                        "evaluation_id": case["id"],
                        "profile_fingerprint": engine.fingerprint,
                        "deadline_ms": 10000,
                        "state": case["state"],
                        "questions": [
                            question_contract(
                                case["purpose"], questions["baseline"][case["purpose"]]
                            )
                        ],
                    }
                )["answers"][0]

            report["rows"] = run_rows(
                [c for c in cases if c["split"] == "validation"],
                evaluate,
                output / "validation-semif.json",
            )
            report["metrics"] = metrics(report["rows"])
        finally:
            engine.close()
    elif args.stage == "resources":
        if args.server_pid is None:
            raise ValueError("resources requires the owned --server-pid")
        if client.call("/api/ps")["models"]:
            raise RuntimeError("Resource samples require no loaded model")
        report["server_idle"] = process_snapshot(args.server_pid)
        try:
            client.call("/api/decide", {"model": BASE_MODEL, "keep_alive": -1}, timeout=300)
            report["ollaya_loaded"] = process_snapshot(args.server_pid)
        finally:
            client.unload(BASE_MODEL)
        report["server_after_unload"] = process_snapshot(args.server_pid)
        engine = SemifOpenVINO(read(args.profile))
        try:
            engine.prepare()
            report["semif_loaded"] = process_snapshot(engine.process.pid)
            case = next(c for c in cases if c["split"] == "validation")
            report["semif_fixed_sample_repetition_ms"] = []
            for index in range(3):
                started = time.perf_counter()
                engine.evaluate(
                    {
                        "evaluation_id": f"resource-{index}",
                        "profile_fingerprint": engine.fingerprint,
                        "deadline_ms": 10000,
                        "state": case["state"],
                        "questions": [
                            question_contract(
                                case["purpose"], questions["baseline"][case["purpose"]]
                            )
                        ],
                    }
                )
                report["semif_fixed_sample_repetition_ms"].append(
                    (time.perf_counter() - started) * 1000
                )
            report["semif_after_inference"] = process_snapshot(engine.process.pid)
            worker_pid = engine.process.pid
        finally:
            engine.close()
        report["semif_after_close"] = {
            "worker_pid": worker_pid,
            "owned_worker_reaped": engine.process is None,
            "working_set_sum_bytes": None,
        }
    else:
        purpose = "relevance"
        report["initial_ps"] = client.call("/api/ps")
        if report["initial_ps"]["models"]:
            raise RuntimeError("Contracts require an idle server")
        client.call("/api/decide", {"model": BASE_MODEL, "keep_alive": -1}, timeout=300)
        try:
            try:
                client.call(
                    "/v1/systemone",
                    {
                        "model": BASE_MODEL,
                        "state": "근거 자료 " * 3000,
                        "questions": {purpose: questions["baseline"][purpose]},
                    },
                    timeout=30,
                )
                report["oversize_rejected"] = False
            except RuntimeError as exc:
                report["oversize_error"] = str(exc)
                report["oversize_rejected"] = "422" in str(exc) and "STATE_TRUNCATED" in str(exc)
            repetitions = []
            case = next(c for c in cases if c["split"] == "validation")
            for _ in range(3):
                started = time.perf_counter()
                client.call(
                    "/v1/systemone",
                    {
                        "model": BASE_MODEL,
                        "state": case["state"],
                        "questions": {purpose: questions["baseline"][purpose]},
                    },
                )
                repetitions.append((time.perf_counter() - started) * 1000)
            report["fixed_sample_repetition_ms"] = repetitions
            client.call("/api/decide", {"model": BASE_MODEL, "keep_alive": "1s"})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and client.call("/api/ps")["models"]:
                time.sleep(0.25)
            report["idle_unloaded"] = not client.call("/api/ps")["models"]
        finally:
            report["explicit_unload"] = client.unload(BASE_MODEL)
    save(stage_path, report)
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k in {"stage", "metrics", "selection", "oversize_rejected", "idle_unloaded"}
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
