"""Bounded CPU head training from frozen train data or an explicitly selected public pilot."""

import argparse
import ctypes
import hashlib
import json
import math
import os
import random
import shutil
import statistics
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def validate_run(args, cases):
    if args.data_profile == "synthetic-learning-curve":
        if not 0 < args.max_run_seconds <= 5400 or args.split not in {"train", "development"}:
            raise ValueError("Curve cache/run limits or split are unapproved")
        if args.stage == "cache":
            if args.train_ids or args.diagnostic_ids or args.epochs or args.feature_cache:
                raise ValueError("Cache cannot accept training selections")
            return cases
        ids = read(args.train_ids) if args.train_ids else []
        by_id = {c["id"]: c for c in cases}
        if (
            len(ids) not in {450, 1350, 4050}
            or len(set(ids)) != len(ids)
            or set(ids) - by_id.keys()
            or args.diagnostic_ids
        ):
            raise ValueError("Curve requires an approved unique frozen train subset")
        approved = read(args.manifest)["train_subsets"].get(str(len(ids)))
        if ids != approved:
            raise ValueError("Curve subset differs from the fixed manifest")
        allowed = {450: {6, 54}, 1350: {6, 18}, 4050: {6}}
        if (
            args.epochs not in allowed[len(ids)]
            or args.learning_rate != 6e-4
            or args.seed not in {20260928, 20260929, 20260930}
            or args.effective_batch != 15
            or not args.feature_cache
            or not 0 < args.max_run_seconds <= 5400
        ):
            raise ValueError("Unapproved learning-curve training settings")
        return [by_id[i] for i in ids]
    if (
        getattr(args, "epochs", None) is not None
        or getattr(args, "train_ids", None)
        or getattr(args, "feature_cache", None)
        or getattr(args, "effective_batch", 1) != 1
        or getattr(args, "seed", 20260928) != 20260928
        or getattr(args, "max_run_seconds", 1200) != 1200
    ):
        raise ValueError("Curve options cannot change an existing training profile")
    steps = getattr(args, "steps", 100)
    rate = getattr(args, "learning_rate", 1e-5)
    extra = args.data_profile == "synthetic-experiment" and steps == 300 and rate in {1e-4, 6e-4}
    if not extra and (steps, rate) not in {(100, 1e-5), (300, 1e-5), (300, 3e-5)}:
        raise ValueError("Only approved A/B/C training settings are allowed")
    selection = getattr(args, "diagnostic_ids", None)
    if not selection:
        return cases
    if args.data_profile == "public-pilot" or rate not in {1e-5, 1e-4, 6e-4} or steps != 300:
        raise ValueError(
            "Diagnostic requires frozen train, 300 steps and an approved diagnostic rate"
        )
    ids = read(selection)
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError("Diagnostic IDs must be a JSON string list")
    if len(ids) != 15 or len(set(ids)) != len(ids):
        raise ValueError("Diagnostic needs 15 unique train IDs")
    by_id = {c["id"]: c for c in cases}
    if set(ids) - by_id.keys():
        raise ValueError("Diagnostic contains non-train or unknown IDs")
    selected = [by_id[i] for i in ids]
    from scripts.prepare_laya_training_data import LABELS

    for purpose, labels in LABELS.items():
        group = [c for c in selected if c["purpose"] == purpose]
        if len(group) != 5 or {c["expected"] for c in group} != set(labels):
            raise ValueError("Diagnostic must cover five cases and all labels per purpose")
    return selected


def select_parity(cases, ids, profile):
    by_id = {c["id"]: c for c in cases}
    if (
        profile == "public-pilot"
        or not isinstance(ids, list)
        or not all(isinstance(i, str) for i in ids)
        or len(ids) != 15
        or len(set(ids)) != 15
        or set(ids) - by_id.keys()
    ):
        raise ValueError("Parity IDs require 15 unique frozen train IDs")
    return [by_id[i] for i in ids]


def diagnostic_passed(snapshots, changed_tensors):
    before, after = snapshots[0], snapshots[-1]
    return bool(
        after["step"] == 300
        and changed_tensors
        and not any(n.startswith("encoder.") for n in changed_tensors)
        and all(math.isfinite(s["mean_loss"]) for s in snapshots)
        and after["mean_loss"] < before["mean_loss"]
        and (after["correct"] > before["correct"] or after["correct"] >= 14)
    )


def training_inputs(args):
    from scripts.prepare_laya_training_data import load_frozen

    if getattr(args, "stage", "train") == "cache" and args.split in {"train", "development"}:
        cases = load_frozen(args.dataset, args.manifest, args.split, args.data_profile)
        return cases, {c["purpose"]: c["question"] for c in cases}
    if args.split != "train":
        raise ValueError("Training/reload parity must use train only")
    if args.data_profile == "public-pilot":
        if args.dataset or args.manifest:
            raise ValueError("Public pilot cannot accept a frozen dataset")
        questions = read(ROOT / "evaluations/ollaya-tuning/candidates.json")["a_original"][
            "questions"
        ]
        return read(ROOT / "evaluations/ollaya-tuning/development.json")["cases"], questions
    if not args.dataset or not args.manifest or not args.data_profile:
        raise ValueError("Explicit --dataset, --manifest, --data-profile required")
    if (
        getattr(args, "stage", "train") == "train"
        and (args.dataset.parent / "test-consumed.json").exists()
    ):
        raise ValueError(
            "Final test consumed: candidate revision requires a new test and frozen dataset"
        )
    cases = load_frozen(args.dataset, args.manifest, args.split, args.data_profile)
    questions = {}
    for case in cases:
        if case["purpose"] in questions and questions[case["purpose"]] != case["question"]:
            raise ValueError("Question changed within purpose")
        questions[case["purpose"]] = case["question"]
    return validate_run(args, cases), questions


def cached_logits(model, item):
    """The installed DecisionModel's decision path, over one immutable FP32 feature row."""
    import torch

    h = item["h"] + model.type_emb(item["qtype"])[:, None, :]
    if model.head is not None:
        for layer in model.head.layers:
            h = layer(h, src_key_padding_mask=~item["attention_mask"].bool())
    idx = item["marker_pos"].clamp(min=0)[:, :, None].expand(-1, -1, h.size(-1))
    return (
        model.scorer(torch.gather(h, 1, idx))
        .squeeze(-1)
        .float()
        .masked_fill(~item["marker_mask"], -1e4)
    )


def check_feature_cache(path, cases, base_hash, tokenizer_files, dataset_hash, split):
    from scripts.prepare_laya_training_data import digest

    meta = read(Path(path) / "cache.json")
    if (
        meta.get("status") != "completed"
        or meta.get("base_sha256") != base_hash
        or meta.get("tokenizer_files") != tokenizer_files
        or meta.get("dataset_sha256") != dataset_hash
        or meta.get("split") != split
        or meta.get("dtype") != "float32"
    ):
        raise ValueError("Cache base/tokenizer/dataset/split/dtype mismatch")
    by_id = {r["id"]: r for r in meta["rows"]}
    if len(by_id) != len(meta["rows"]):
        raise ValueError("Duplicate feature cache IDs")
    for c in cases:
        row = by_id.get(c["id"], {})
        if row.get("state_sha256") != digest(c["state"]) or row.get("question_sha256") != digest(
            c["question"]
        ):
            raise ValueError("Cached input/question changed")
        target = (Path(path) / row["file"]).resolve()
        if target.parent != Path(path).resolve() or sha(target) != row["sha256"]:
            raise ValueError("Cached feature file changed or escaped its directory")
    return by_id


def cache_features(args, agent, cases, report, output, deadline, started):
    import torch
    from laya.common import build_sequence, collate_items, render_options, serialize_state

    from scripts.prepare_laya_training_data import digest

    report.update(dtype="float32", rows=[], split=args.split, parity=[])
    development = []
    feature_started = time.perf_counter()
    saved_bytes = 0
    agent.model.eval()
    for index, case in enumerate(cases):
        if memory_gib() < 1.5 or time.perf_counter() - started > args.max_run_seconds:
            raise RuntimeError("Feature cache resource limit")
        question = agent._to_internal(case["question"])
        text = serialize_state(case["state"])
        if len(agent.tok.encode(text, add_special_tokens=False)) > 1024:
            raise ValueError("Feature input exceeds 1024 tokens")
        seq, markers = build_sequence(agent.tok, case["state"], question, 1024, 256)
        full, _ = build_sequence(agent.tok, case["state"], question, 32768, 32768)
        if seq != full or len(markers) != len(render_options(question)):
            raise ValueError("Feature input/options would be truncated")
        batch = collate_items(
            [[{"ids": seq, "markers": markers, "qtype": 0}]], agent.tok.pad_token_id
        )
        deadline["step"] = time.perf_counter() + 30
        with torch.no_grad():
            h = agent.model.encoder(
                input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]
            ).last_hidden_state
            item = {k: batch[k] for k in ("attention_mask", "marker_pos", "marker_mask", "qtype")}
            item["h"] = h.detach().float().clone()
            item["label"] = list(question["crit"]).index(case["expected"])
            if args.split == "development":
                labels = list(question["crit"])
                z = cached_logits(agent.model, item)[0, : len(labels)]
                development.append(
                    {
                        k: case[k]
                        for k in (
                            "id",
                            "purpose",
                            "expected",
                            "critical",
                            "group_id",
                            "state_sha256",
                        )
                    }
                    | dict(
                        split="development",
                        status="observed",
                        repeat=0,
                        question_sha256=digest(case["question"]),
                        choice=labels[int(z.argmax())],
                        probabilities=dict(zip(labels, z.softmax(-1).tolist(), strict=True)),
                        latency_ms=0,
                    )
                )
            if index < 15 and args.split == "train":
                direct = agent.model(
                    **{
                        k: batch[k]
                        for k in (
                            "input_ids",
                            "attention_mask",
                            "marker_pos",
                            "marker_mask",
                            "qtype",
                        )
                    }
                )[0]
                cached = cached_logits(agent.model, item)
                error = float((direct.softmax(-1) - cached.softmax(-1)).abs().max())
                match = int(direct.argmax()) == int(cached.argmax())
                report["parity"].append(dict(id=case["id"], choice_match=match, max_error=error))
                if not match or error > 0.0001:
                    raise ValueError("FP32 feature cache parity failed")
        deadline["step"] = None
        target = output / f"row-{index:05d}.pt"
        torch.save(item, target)
        saved_bytes += target.stat().st_size
        report["rows"].append(
            dict(
                id=case["id"],
                file=target.name,
                sha256=sha(target),
                state_sha256=digest(case["state"]),
                question_sha256=digest(case["question"]),
                token_sha256=digest(seq),
                tokens=len(seq),
            )
        )
        if index % 100 == 0:
            save(output / "cache.json", report)
            print(f"cached {index + 1}/{len(cases)}", flush=True)
        if index == 14 and args.split == "train":
            began = time.perf_counter()
            deadline["step"] = began + 30
            for name, param in agent.model.named_parameters():
                param.requires_grad_(name.startswith(("head.", "type_emb.", "scorer.")))
            agent.model.train()
            agent.model.encoder.eval()
            for row in report["rows"]:
                probe = torch.load(output / row["file"], weights_only=True)
                loss = torch.nn.functional.cross_entropy(
                    cached_logits(agent.model, probe), torch.tensor([probe["label"]])
                )
                (loss / 15).backward()
            norm = torch.nn.utils.clip_grad_norm_(
                [p for p in agent.model.parameters() if p.requires_grad], 1, error_if_nonfinite=True
            )
            agent.model.zero_grad(set_to_none=True)
            agent.model.eval()
            deadline["step"] = None
            elapsed = time.perf_counter() - began
            report["preflight"] = dict(
                head_batch15_forward_backward_seconds=elapsed,
                gradient_norm=float(norm),
                optimizer_updates=0,
                estimated_training_seconds=elapsed * 16740,
                estimated_cache_seconds=(began - feature_started) / 15 * 4500,
            )
            save(output / "cache.json", report)
            print(f"preflight: {json.dumps(report['preflight'])}", flush=True)
        if saved_bytes > 40 * 2**30:
            raise RuntimeError("Feature cache exceeds disk budget")
    report["feature_seconds"] = time.perf_counter() - feature_started
    if sha(args.base / "model.safetensors") != report["base_sha256"]:
        raise ValueError("Original encoder/model changed during caching")
    if development:
        from scripts.evaluate_ollaya import metrics

        save(
            output / "baseline-development.json",
            dict(
                status="completed",
                data_profile=args.data_profile,
                split="development",
                dataset_sha256=report["dataset_sha256"],
                rows=development,
                metrics=metrics(development),
            ),
        )


def forecast_curve_run(root, ledger, size, epochs, base_hash, max_seconds=5400):
    """Estimate the next run from comparable observed timing, never from accuracy."""
    observations = []
    root = Path(root).resolve()
    for run in ledger["runs"]:
        path = Path(run.get("report", "")).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        report = read(path)
        watchdog_path = path.parent / "watchdog-abort.json"
        watchdog = read(watchdog_path) if watchdog_path.exists() else {}
        usable = report.get("status") == "completed" or (
            run.get("exit_code") == 124
            and watchdog.get("elapsed_seconds", 0) >= max_seconds
        )
        if not usable or (
            report.get("diagnostic")
            or report.get("data_profile") != "synthetic-learning-curve"
            or report.get("dataset_sha256") != ledger["dataset_sha256"]
            or report.get("trainer_sha256") != ledger["trainer_sha256"]
            or report.get("base_sha256") != base_hash
            or report.get("device") != "cpu"
            or report.get("threads") != 4
            or report.get("batch_size") != 15
            or report.get("microbatch_size") != 1
            or len(report.get("training_ids", [])) != size
        ):
            continue
        values = [s.get("seconds") for s in report.get("steps", [])]
        if len(values) < 30 or any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v <= 30
            for v in values
        ):
            continue
        observations.append((statistics.median(values), run["name"]))
    if not observations:
        return None
    seconds, source = min(observations)
    updates = size // 15 * epochs
    return {
        "estimated_optimizer_seconds": seconds * updates,
        "seconds_per_update": seconds,
        "planned_updates": updates,
        "source_run": source,
        "excludes_load_and_evaluation": True,
        "uncertainty": "Timing forecast, not a guarantee; CPU load can change.",
    }


def stop_owned_process(process):
    """Stop only the subprocess tree created by this coordinator."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        process.kill()
    process.wait()


def run_curve(args):
    """Sequential bounded coordinator; persistent elapsed budget cannot reset on resume."""
    from scripts.prepare_laya_training_data import load_frozen

    if args.data_profile != "synthetic-learning-curve" or args.split != "train":
        raise ValueError("Curve coordinator requires the new frozen profile")
    train = load_frozen(args.dataset, args.manifest, "train", args.data_profile)
    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    ledger_path = root / "execution.json"
    ledger = (
        read(ledger_path)
        if ledger_path.exists()
        else {
            "started_at_unix": time.time(),
            "limit_seconds": 43200,
            "final_reserve_seconds": 7200,
            "dataset_sha256": sha(args.dataset),
            "trainer_sha256": sha(Path(__file__)),
            "runs": [],
            "status": "running",
        }
    )
    if ledger["dataset_sha256"] != sha(args.dataset) or ledger["trainer_sha256"] != sha(
        Path(__file__)
    ):
        raise ValueError("Coordinator dataset or code changed on resume")
    artifact_roots = [Path(p).resolve() for p in ledger.get("artifact_roots", [str(root)])]
    if any(
        not p.is_relative_to((ROOT / ".local/laya-finetuning").resolve()) for p in artifact_roots
    ):
        raise ValueError("Artifact budget roots escaped the local experiment directory")
    save(ledger_path, ledger)
    manifest = read(args.manifest)
    for size, ids in manifest["train_subsets"].items():
        target = root / f"train-{size}.json"
        if target.exists() and read(target) != ids:
            raise ValueError("Fixed subset changed")
        save(target, ids)
    parity = [c["id"] for c in train[:15]]
    save(root / "parity-ids.json", parity)
    common = [
        "--dataset",
        str(args.dataset.resolve()),
        "--manifest",
        str(args.manifest.resolve()),
        "--data-profile",
        args.data_profile,
        "--max-run-seconds",
        "5400",
    ]

    def execute(name, command, report_path):
        if report_path.exists() and read(report_path).get("status") == "completed":
            return True
        if any(r["name"] == name for r in ledger["runs"]):
            return False
        elapsed = time.time() - ledger["started_at_unix"]
        disk = sum(
            p.stat().st_size
            for directory in artifact_roots
            for p in directory.rglob("*")
            if p.is_file()
        )
        needed = (args.base / "model.safetensors").stat().st_size * 2
        if elapsed >= 43200 - 7200 or disk + needed > 40 * 2**30:
            ledger["runs"].append(dict(name=name, status="not_run", reason="time_or_disk_budget"))
            save(ledger_path, ledger)
            return False
        began = time.time()
        log = root / (name + ".log")
        with log.open("x", encoding="utf-8") as stream:
            process = subprocess.Popen(
                [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), *command],
                cwd=ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
            try:
                code = process.wait(timeout=min(5500, 43200 - 7200 - elapsed))
            except subprocess.TimeoutExpired:
                stop_owned_process(process)
                code = 124
        result = read(report_path) if report_path.exists() else {}
        status = (
            "completed"
            if code == 0 and result.get("status") == "completed"
            else "incomplete"
            if code == 124
            else "failed"
        )
        watchdog_path = report_path.parent / "watchdog-abort.json"
        watchdog = read(watchdog_path) if watchdog_path.exists() else {}
        ledger["runs"].append(
            dict(
                name=name,
                status=status,
                exit_code=code,
                seconds=time.time() - began,
                report=str(report_path),
                reason=result.get("error")
                or watchdog.get("reason")
                or ("subprocess_time_limit" if code == 124 else None),
                watchdog_abort=watchdog,
            )
        )
        save(ledger_path, ledger)
        disk_after = sum(
            p.stat().st_size
            for directory in artifact_roots
            for p in directory.rglob("*")
            if p.is_file()
        )
        ledger["local_bytes"] = disk_after
        if disk_after > 40 * 2**30:
            ledger.update(status="blocked", reason="disk_budget_exceeded")
            save(ledger_path, ledger)
            raise RuntimeError("Curve artifact budget exceeded")
        print(f"{name}: {status}, {time.time() - began:.1f}s", flush=True)
        return status == "completed"

    for split in ("train", "development"):
        path = root / "features" / split
        if not execute(
            "cache-" + split,
            ["cache", *common, "--split", split, "--output", str(path.resolve())],
            path / "cache.json",
        ):
            ledger.update(status="blocked", reason="feature_cache_not_complete")
            save(ledger_path, ledger)
            return
    # Original development predictions are produced without any optimizer updates.
    cache_meta = read(root / "features/train/cache.json")
    ledger["preflight"] = {
        "cache_parity": cache_meta["parity"],
        "feature_seconds": cache_meta["feature_seconds"],
        "estimated_encoder_seconds_per_row": cache_meta["feature_seconds"] / 4050,
    }
    save(ledger_path, ledger)
    for size, epochs in ((450, 6), (1350, 6), (4050, 6), (450, 54), (1350, 18)):
        for seed in (20260928, 20260929, 20260930):
            name = f"n{size}-e{epochs}-s{seed}"
            path = root / name
            if not any(r["name"] == name for r in ledger["runs"]):
                forecast = forecast_curve_run(root, ledger, size, epochs, cache_meta["base_sha256"])
                remaining = 36000 - (time.time() - ledger["started_at_unix"])
                if forecast and forecast["estimated_optimizer_seconds"] > min(5400, remaining):
                    ledger["runs"].append(
                        dict(
                            name=name,
                            status="not_run",
                            reason="estimated_runtime_exceeds_run_or_training_budget",
                            report=str(path / "train.json"),
                            runtime_forecast=forecast,
                        )
                    )
                    save(ledger_path, ledger)
                    print(f"{name}: not_run, observed timing exceeds remaining limits", flush=True)
                    continue
            ok = execute(
                name,
                [
                    "train",
                    *common,
                    "--split",
                    "train",
                    "--output",
                    str(path.resolve()),
                    "--train-ids",
                    str((root / f"train-{size}.json").resolve()),
                    "--epochs",
                    str(epochs),
                    "--seed",
                    str(seed),
                    "--learning-rate",
                    "0.0006",
                    "--effective-batch",
                    "15",
                    "--feature-cache",
                    str((root / "features/train").resolve()),
                    "--parity-ids",
                    str((root / "parity-ids.json").resolve()),
                ],
                path / "train.json",
            )
            if ok and "estimated_training_seconds" not in ledger:
                step = read(path / "train.json")["step_median_seconds"]
                ledger["estimated_training_seconds"] = step * 16740
                save(ledger_path, ledger)
            if not ok and memory_gib() < 4:
                ledger.update(status="blocked", reason="memory_not_recovered")
                save(ledger_path, ledger)
                return
    ledger.update(
        status="development_complete"
        if all(r["status"] == "completed" for r in ledger["runs"] if r["name"].startswith("n"))
        else "development_incomplete",
        elapsed_seconds=time.time() - ledger["started_at_unix"],
    )
    save(ledger_path, ledger)


def curve_train(args, agent, cases, report, deadline, started):
    import torch

    from scripts.evaluate_ollaya import metrics
    from scripts.prepare_laya_training_data import digest, load_frozen

    dev = load_frozen(args.dataset, args.manifest, "development", args.data_profile)
    pending = [
        {k: c[k] for k in ("id", "purpose", "expected", "critical", "group_id", "state_sha256")}
        | dict(
            split="development",
            repeat=0,
            question_sha256=digest(c["question"]),
            status="not_run",
            reason="training_not_complete",
        )
        for c in dev
    ]
    dev_report = dict(
        status="not_run",
        data_profile=args.data_profile,
        split="development",
        dataset_sha256=report["dataset_sha256"],
        rows=pending,
    )
    save(args.output / "development.json", dev_report)

    cache = check_feature_cache(
        args.feature_cache,
        cases,
        report["base_sha256"],
        report["tokenizer_files"],
        report["dataset_sha256"],
        "train",
    )
    parity = read(args.feature_cache / "cache.json").get("parity", [])
    if len(parity) != 15 or any(not p["choice_match"] or p["max_error"] > 0.0001 for p in parity):
        raise ValueError("Curve cache requires fifteen parity observations")
    for p in agent.model.parameters():
        p.requires_grad_(False)
    prefixes = ("head.", "type_emb.", "scorer.")
    parameters = [p for n, p in agent.model.named_parameters() if n.startswith(prefixes)]
    for p in parameters:
        p.requires_grad_(True)
    opt = torch.optim.AdamW(parameters, lr=args.learning_rate, weight_decay=0.01)
    initial = {
        n: p.detach().clone() for n, p in agent.model.named_parameters() if n.startswith(prefixes)
    }
    report.update(
        steps=[],
        exposures={c["id"]: 0 for c in cases},
        epochs=args.epochs,
        batch_size=15,
        microbatch_size=1,
        max_steps=len(cases) // 15 * args.epochs,
    )
    for epoch in range(args.epochs):
        order = list(cases)
        random.shuffle(order)
        for offset in range(0, len(order), 15):
            if memory_gib() < 1.5 or time.perf_counter() - started > args.max_run_seconds:
                raise RuntimeError("Curve run resource limit")
            began = time.perf_counter()
            deadline["step"] = began + 30
            agent.model.train()
            agent.model.encoder.eval()
            opt.zero_grad(set_to_none=True)
            losses, ids = [], []
            for case in order[offset : offset + 15]:
                item = torch.load(args.feature_cache / cache[case["id"]]["file"], weights_only=True)
                if (
                    item["h"].dtype != torch.float32
                    or not torch.isfinite(item["h"]).all()
                    or item["label"] != list(case["question"]["criteria"]).index(case["expected"])
                ):
                    raise ValueError("Feature dtype/value/label mismatch")
                z = cached_logits(agent.model, item)
                loss = torch.nn.functional.cross_entropy(z, torch.tensor([item["label"]]))
                if not torch.isfinite(loss):
                    raise ValueError("Non-finite cached training loss")
                (loss / 15).backward()
                losses.append(float(loss.detach()))
                ids.append(case["id"])
                report["exposures"][case["id"]] += 1
            norm = torch.nn.utils.clip_grad_norm_(parameters, 1, error_if_nonfinite=True)
            opt.step()
            deadline["step"] = None
            elapsed = time.perf_counter() - began
            if elapsed > 30 or any(not torch.isfinite(p).all() for p in parameters):
                raise ValueError("Invalid update/time limit")
            report["steps"].append(
                dict(
                    step=len(report["steps"]) + 1,
                    epoch=epoch + 1,
                    case_ids=ids,
                    loss=statistics.mean(losses),
                    seconds=elapsed,
                    gradient_norm=float(norm),
                )
            )
            if len(report["steps"]) % 30 == 0:
                from scripts.evaluate_ollaya import process_snapshot

                report.setdefault("memory_samples", []).append(process_snapshot(os.getpid()))
                save(args.output / "train.json", report)
                print(f"curve step {len(report['steps'])}/{report['max_steps']}", flush=True)
    if set(report["exposures"].values()) != {args.epochs}:
        raise ValueError("Curve exposure counts differ from the approved epochs")
    report["module_update_norms"] = {
        prefix: math.sqrt(
            sum(
                float((p.detach() - initial[n]).square().sum())
                for n, p in agent.model.named_parameters()
                if n.startswith(prefix)
            )
        )
        for prefix in prefixes
    }
    if not all(math.isfinite(v) and v > 0 for v in report["module_update_norms"].values()):
        raise ValueError("Curve modules did not update normally")
    report["step_median_seconds"] = statistics.median(s["seconds"] for s in report["steps"])
    # Development must describe the stored candidate, including the base storage precision.
    _, header = safetensors_header(args.base / "model.safetensors")
    dtypes = {"F16": torch.float16, "F32": torch.float32, "BF16": torch.bfloat16}
    with torch.no_grad():
        for name, param in agent.model.named_parameters():
            if name.startswith(prefixes):
                restored = param.to(dtypes[header[name]["dtype"]]).float()
                if not torch.isfinite(restored).all():
                    raise ValueError("Non-finite stored candidate parameter")
                param.copy_(restored)
    report["development_precision"] = "checkpoint storage round-trip restored to FP32"
    # Development predictions are generated by the same frozen-feature path, never used in loss.
    dev_path = args.feature_cache.parent / "development"
    dev_cache = check_feature_cache(
        dev_path,
        dev,
        report["base_sha256"],
        report["tokenizer_files"],
        report["dataset_sha256"],
        "development",
    )
    rows = []
    agent.model.eval()
    with torch.no_grad():
        for case in dev:
            if memory_gib() < 1.5 or time.perf_counter() - started > args.max_run_seconds:
                raise RuntimeError("Development resource limit")
            deadline["step"] = time.perf_counter() + 30
            item = torch.load(dev_path / dev_cache[case["id"]]["file"], weights_only=True)
            labels = list(case["question"]["criteria"])
            z = cached_logits(agent.model, item)[0, : len(labels)]
            probs = z.softmax(-1).tolist()
            if any(not math.isfinite(p) for p in probs):
                raise ValueError("Non-finite development output")
            deadline["step"] = None
            rows.append(
                {
                    k: case[k]
                    for k in ("id", "purpose", "expected", "critical", "group_id", "state_sha256")
                }
                | {
                    "split": "development",
                    "status": "observed",
                    "repeat": 0,
                    "question_sha256": dev_cache[case["id"]]["question_sha256"],
                    "choice": labels[int(z.argmax())],
                    "probabilities": dict(zip(labels, probs, strict=True)),
                    "latency_ms": 0,
                }
            )
            if len(rows) % 30 == 0:
                dev_report.update(status="running", rows=rows + pending[len(rows) :])
                save(args.output / "development.json", dev_report)
    save(
        args.output / "development.json",
        dict(
            status="completed",
            data_profile=args.data_profile,
            split="development",
            dataset_sha256=report["dataset_sha256"],
            rows=rows,
            metrics=metrics(rows),
        ),
    )


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safetensors_header(path):
    with Path(path).open("rb") as stream:
        size = struct.unpack("<Q", stream.read(8))[0]
        if size > 16 * 1024 * 1024:
            raise ValueError("Unexpected safetensors header size")
        header = json.loads(stream.read(size))
    return size + 8, {k: v for k, v in header.items() if k != "__metadata__"}


def memory_gib():
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong)
            for name in ("physical", "available", "page", "available_page", "virtual", "av", "ex")
        ]

    value = Memory()
    value.length = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        raise OSError("Cannot read available memory")
    return value.available / 2**30


def compatible_encoder_config(config):
    """Map Transformers 5 ModernBERT rotary fields to the pinned 4.56 reference."""
    config = dict(config)
    rope = config.get("rope_parameters")
    if not rope:
        return config, {}
    overrides = {}
    for kind, legacy in (
        ("full_attention", "global_rope_theta"),
        ("sliding_attention", "local_rope_theta"),
    ):
        value = rope.get(kind, {})
        if value.get("rope_type") != "default" or not isinstance(
            value.get("rope_theta"), (int, float)
        ):
            raise ValueError("Unsupported rotary configuration")
        overrides[legacy] = value["rope_theta"]
        if legacy in config and config[legacy] != overrides[legacy]:
            raise ValueError("Conflicting rotary configuration")
    every = config.get("global_attn_every_n_layers", 3)
    expected = [
        "full_attention" if i % every == 0 else "sliding_attention"
        for i in range(config["num_hidden_layers"])
    ]
    if config.get("layer_types", expected) != expected:
        raise ValueError("Unsupported attention layer layout")
    config.update(overrides)
    return config, overrides


def rewrite_weights(base, output, tensors):
    """Keep original half-precision storage offsets; fail on any incompatible tensor."""
    import torch

    offset, header = safetensors_header(base)
    if set(header) != set(tensors):
        raise ValueError("Checkpoint tensor names changed")
    shutil.copyfile(base, output)
    dtypes = {"F16": torch.float16, "F32": torch.float32, "BF16": torch.bfloat16}
    changes = []
    with Path(base).open("rb") as original, Path(output).open("r+b") as dest:
        for name, info in header.items():
            tensor = tensors[name].detach().cpu().contiguous()
            if list(tensor.shape) != info["shape"] or info["dtype"] not in dtypes:
                raise ValueError(f"Incompatible tensor: {name}")
            payload = tensor.to(dtypes[info["dtype"]]).view(torch.uint8).numpy().tobytes()
            start, end = info["data_offsets"]
            if len(payload) != end - start:
                raise ValueError(f"Tensor byte length changed: {name}")
            original.seek(offset + start)
            previous = original.read(len(payload))
            if payload != previous:
                changes.append(name)
                dest.seek(offset + start)
                dest.write(payload)
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["train", "reload", "cache", "curve"])
    parser.add_argument("--base", type=Path, default=ROOT / ".local/models/laya-multilingual")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/laya-finetuning/pilot")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--diagnostic-ids", type=Path)
    parser.add_argument("--parity-ids", type=Path)
    parser.add_argument("--train-ids", type=Path)
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--seed", type=int, default=20260928)
    parser.add_argument("--feature-cache", type=Path)
    parser.add_argument("--effective-batch", type=int, default=1)
    parser.add_argument("--max-run-seconds", type=int, default=1200)
    parser.add_argument(
        "--split", choices=["train", "development", "calibration", "test"], required=True
    )
    parser.add_argument(
        "--data-profile",
        choices=["actual", "synthetic-experiment", "synthetic-learning-curve", "public-pilot"],
        required=True,
    )
    args = parser.parse_args()
    if args.stage == "curve":
        from jev_context.engines import FileLock

        args.output.mkdir(parents=True, exist_ok=True)
        with FileLock(args.output / "coordinator.lock"):
            run_curve(args)
        return
    cases, questions = training_inputs(args)
    validate_run(args, cases)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from laya import Agent
    from laya.common import build_sequence, collate_items, render_options, serialize_state

    from jev_context.storage import FileLock

    torch.set_num_threads(4)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    with (output / (args.stage + ".started")).open("x") as marker:
        marker.write(str(time.time()))
    report = {
        "stage": args.stage,
        "quality_evaluation": False,
        "promotion_eligible": False,
        "data_profile": args.data_profile,
        "split": args.split,
        "training_ids": [c["id"] for c in cases],
        "dataset_sha256": sha(args.dataset) if args.dataset else None,
        "manifest_sha256": sha(args.manifest) if args.manifest else None,
        "seed": args.seed,
        "batch_size": 1,
        "threads": 4,
        "learning_rate": args.learning_rate,
        "max_steps": args.steps,
        "diagnostic": bool(args.diagnostic_ids),
        "diagnostic_ids_sha256": sha(args.diagnostic_ids) if args.diagnostic_ids else None,
        "trainer_sha256": sha(Path(__file__)),
        "questions": questions,
    }
    started = time.perf_counter()
    stopped = threading.Event()
    deadline = {"step": None}

    def watchdog():
        while not stopped.wait(0.5):
            now = time.perf_counter()
            if (
                now - started > args.max_run_seconds
                or (deadline["step"] and now > deadline["step"])
                or memory_gib() < 1.5
            ):
                save(
                    output / "watchdog-abort.json",
                    {
                        "status": "failed",
                        "reason": "time_or_memory_limit",
                        "elapsed_seconds": now - started,
                    },
                )
                os._exit(124)

    guard = FileLock(ROOT / ".local/laya-finetuning/experiment-model.lock")
    resident = FileLock(
        Path(read(ROOT / ".local/semif-ov-profile.json")["lock_root"]) / "resident.lock"
    )
    guard.__enter__()
    try:
        resident.__enter__()
    except BaseException:
        guard.__exit__(None, None, None)
        raise
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        if memory_gib() < 4:
            raise MemoryError("Require 4 GiB available before load")
        base_weights = args.base / "model.safetensors"
        original_hash = sha(base_weights)
        parity = [c for p in questions for c in [x for x in cases if x["purpose"] == p][:4]]
        if args.parity_ids:
            parity = select_parity(cases, read(args.parity_ids), args.data_profile)
            report["parity_ids_sha256"] = sha(args.parity_ids)
        if args.stage in {"train", "cache"}:
            model_path = (
                output.parent / "reference-base"
                if args.data_profile == "synthetic-learning-curve"
                else output / "reference-base"
            )
            if not model_path.exists():
                shutil.copytree(args.base, model_path)
            elif sha(model_path / "model.safetensors") != original_hash:
                raise ValueError("Compatible reference weights changed")
            cfg, overrides = compatible_encoder_config(read(model_path / "encoder/config.json"))
            save(model_path / "encoder/config.json", cfg)
            report["reference_config_overrides"] = overrides
        else:
            model_path = output / "checkpoint"
        agent = Agent(str(model_path), device="cpu")
        report["tokenizer_files"] = {
            str(p.relative_to(model_path)): sha(p)
            for p in sorted((model_path / "tokenizer").rglob("*"))
            if p.is_file()
        }
        report["input_audit"] = []
        for case in cases:
            q = agent._to_internal(questions[case["purpose"]])
            state_text = serialize_state(case["state"])
            options = render_options(q)
            if any(agent.tok.mask_token in value for value in [state_text, q["ins"], *options]):
                raise ValueError("Input contains mask token that the runtime would replace")
            lengths = [
                len(agent.tok(" " + option, add_special_tokens=False)["input_ids"])
                for option in options
            ]
            head = len(
                agent.tok(f"{q['t']} question: {q['ins']}", add_special_tokens=False)["input_ids"]
            )
            state_length = len(agent.tok(state_text, add_special_tokens=False)["input_ids"])
            option_total = sum(1 + length for length in lengths)
            if (
                any(length > 48 for length in lengths)
                or max(head, 16) + option_total > 256
                or head + option_total + state_length + 4 > 1024
            ):
                raise ValueError("Question/options/state would be truncated")
            seq, markers = build_sequence(agent.tok, case["state"], q, 1024, 256)
            report["input_audit"].append(
                {
                    "id": case["id"],
                    "labels": list(q["crit"]),
                    "label_index": list(q["crit"]).index(case["expected"]),
                    "state_tokens": state_length,
                    "head_tokens": head,
                    "option_tokens": lengths,
                    "sequence_tokens": len(seq),
                    "markers": markers,
                }
            )
        report.update(
            base_sha256=original_hash,
            device=str(agent.device),
            torch=torch.__version__,
            load_seconds=time.perf_counter() - started,
            available_gib_after_load=memory_gib(),
        )

        def predictions():
            agent.model.eval()
            return [
                {
                    "id": case["id"],
                    "state": case["state"],
                    "purpose": case["purpose"],
                    "question": questions[case["purpose"]],
                    "answer": agent.system_one(
                        case["state"], {case["purpose"]: questions[case["purpose"]]}
                    )["answers"][case["purpose"]],
                }
                for case in parity
            ]

        if args.stage == "cache":
            cache_features(args, agent, cases, report, output, deadline, started)
            report.update(status="completed", elapsed_seconds=time.perf_counter() - started)
            save(output / "cache.json", report)
            return
        if args.data_profile == "synthetic-learning-curve" and args.stage == "train":
            report["before"] = predictions()
            curve_train(args, agent, cases, report, deadline, started)
            checkpoint = output / "checkpoint"
            checkpoint.mkdir()
            for directory in ("encoder", "tokenizer"):
                shutil.copytree(model_path / directory, checkpoint / directory)
            shutil.copyfile(
                model_path / "rl_agent_config.json", checkpoint / "rl_agent_config.json"
            )
            report["changed_tensors"] = rewrite_weights(
                base_weights, checkpoint / "model.safetensors", agent.model.state_dict()
            )
            if not report["changed_tensors"] or any(
                n.startswith("encoder.") for n in report["changed_tensors"]
            ):
                raise ValueError("Curve encoder changed or no weight update")
            report["checkpoint_sha256"] = sha(checkpoint / "model.safetensors")
            report["after_fp32"] = predictions()
            report.update(status="completed", elapsed_seconds=time.perf_counter() - started)
            if sha(base_weights) != original_hash:
                raise ValueError("Original model changed")
            save(output / "train.json", report)
            return

        if args.stage == "reload":
            report["predictions"] = predictions()
            report["checkpoint_sha256"] = sha(model_path / "model.safetensors")
        else:
            report["before"] = predictions()
            for name, param in agent.model.named_parameters():
                param.requires_grad_(name.startswith(("head.", "type_emb.", "scorer.")))
            trainable = [p for p in agent.model.parameters() if p.requires_grad]
            report["trainable_parameters"] = sum(p.numel() for p in trainable)
            report["total_parameters"] = sum(p.numel() for p in agent.model.parameters())
            optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate, weight_decay=0.01)
            prepared = []
            for case in cases:
                q = agent._to_internal(questions[case["purpose"]])
                seq, markers = build_sequence(agent.tok, case["state"], q, 1024, 256)
                full, _ = build_sequence(agent.tok, case["state"], q, 32768, 256)
                if len(full) != len(seq) or len(markers) != len(render_options(q)):
                    raise ValueError("Training input was truncated")
                prepared.append(
                    {
                        "ids": seq,
                        "markers": markers,
                        "qtype": 0,
                        "label": list(q["crit"]).index(case["expected"]),
                    }
                )
            report["steps"] = []
            report["diagnostic_snapshots"] = []

            def snapshot(step):
                rows = []
                agent.model.eval()
                with torch.inference_mode():
                    for case, item in zip(cases, prepared, strict=True):
                        batch = collate_items([[item]], agent.tok.pad_token_id)
                        z, _ = agent.model(
                            **{
                                k: batch[k]
                                for k in (
                                    "input_ids",
                                    "attention_mask",
                                    "marker_pos",
                                    "marker_mask",
                                    "qtype",
                                )
                            },
                            detach_encoder=True,
                        )
                        z = z[0, : len(item["markers"])]
                        if not torch.isfinite(z).all():
                            raise ValueError("Non-finite diagnostic logits")
                        label = item["label"]
                        probabilities = z.softmax(-1)
                        labels = list(questions[case["purpose"]]["criteria"])
                        api = agent.system_one(
                            case["state"], {case["purpose"]: questions[case["purpose"]]}
                        )["answers"][case["purpose"]]
                        from laya.common import temp_bucket

                        temperature = agent.temperature_by_options.get(
                            temp_bucket(0, len(labels)), agent.temperature[0]
                        )
                        scaled = (z / temperature).softmax(-1)
                        if api["choice"] != labels[int(z.argmax())] or any(
                            abs(api["probabilities"][name] - float(scaled[i])) > 0.00011
                            for i, name in enumerate(labels)
                        ):
                            raise ValueError("Direct logits and Python choice/probabilities differ")
                        rows.append(
                            {
                                "id": case["id"],
                                "expected": case["expected"],
                                "choice": labels[int(z.argmax())],
                                "loss": float(-z.log_softmax(-1)[label]),
                                "correct_probability": float(probabilities[label]),
                                "correct_margin": float(
                                    z[label] - torch.cat((z[:label], z[label + 1 :])).max()
                                ),
                                "logits": z.tolist(),
                                "labels": labels,
                                "python_parity": True,
                            }
                        )
                result = {
                    "step": step,
                    "rows": rows,
                    "mean_loss": statistics.mean(r["loss"] for r in rows),
                    "correct": sum(r["choice"] == r["expected"] for r in rows),
                }
                report["diagnostic_snapshots"].append(result)
                save(output / "train.json", report)

            if args.diagnostic_ids:
                snapshot(0)
            order = list(range(len(prepared)))
            for step in range(args.steps):
                if step % len(order) == 0:
                    random.shuffle(order)
                if memory_gib() < 1.5 or time.perf_counter() - started > 1200:
                    raise RuntimeError("Memory or whole-run time budget exceeded")
                batch = collate_items(
                    [[prepared[order[step % len(order)]]]], agent.tok.pad_token_id
                )
                agent.model.train()
                agent.model.encoder.eval()
                optimizer.zero_grad(set_to_none=True)
                began = time.perf_counter()
                deadline["step"] = began + 30
                logits, _ = agent.model(
                    **{
                        k: batch[k]
                        for k in (
                            "input_ids",
                            "attention_mask",
                            "marker_pos",
                            "marker_mask",
                            "qtype",
                        )
                    },
                    detach_encoder=True,
                )
                loss = torch.nn.functional.cross_entropy(logits, batch["label"])
                if not torch.isfinite(loss):
                    raise ValueError("Non-finite training loss")
                loss.backward()
                modules = ("head.", "type_emb.", "scorer.")
                gradient_norms = {}
                previous = {}
                for prefix in modules:
                    params = [
                        (n, p) for n, p in agent.model.named_parameters() if n.startswith(prefix)
                    ]
                    gradient_norms[prefix] = math.sqrt(
                        sum(
                            float(p.grad.detach().float().square().sum())
                            for _, p in params
                            if p.grad is not None
                        )
                    )
                    previous.update({n: p.detach().clone() for n, p in params})
                if not all(math.isfinite(v) for v in gradient_norms.values()):
                    raise ValueError("Non-finite module gradients")
                total_norm = torch.nn.utils.clip_grad_norm_(trainable, 1.0, error_if_nonfinite=True)
                optimizer.step()
                update_norms = {
                    prefix: math.sqrt(
                        sum(
                            float((p.detach() - previous[n]).float().square().sum())
                            for n, p in agent.model.named_parameters()
                            if n.startswith(prefix)
                        )
                    )
                    for prefix in modules
                }
                if not all(math.isfinite(v) for v in update_norms.values()):
                    raise ValueError("Non-finite parameter update")
                elapsed = time.perf_counter() - began
                deadline["step"] = None
                report["steps"].append(
                    {
                        "step": step + 1,
                        "loss": loss.item(),
                        "seconds": elapsed,
                        "case_id": cases[order[step % len(order)]]["id"],
                        "gradient_norms": gradient_norms,
                        "update_norms": update_norms,
                        "total_gradient_norm_before_clip": float(total_norm),
                    }
                )
                save(output / "train.json", report)
                print(
                    f"step {step + 1}/{args.steps} loss={loss.item():.4f} seconds={elapsed:.3f}",
                    flush=True,
                )
                if elapsed > 30:
                    raise TimeoutError("Step exceeded 30 seconds")
                if args.diagnostic_ids and step + 1 in (100, 300):
                    snapshot(step + 1)
            checkpoint = output / "checkpoint"
            checkpoint.mkdir()
            for directory in ("encoder", "tokenizer"):
                shutil.copytree(model_path / directory, checkpoint / directory)
            shutil.copyfile(
                model_path / "rl_agent_config.json", checkpoint / "rl_agent_config.json"
            )
            report["changed_tensors"] = rewrite_weights(
                base_weights, checkpoint / "model.safetensors", agent.model.state_dict()
            )
            if not report["changed_tensors"] or any(
                n.startswith("encoder.") for n in report["changed_tensors"]
            ):
                raise ValueError("Unexpected weight updates")
            report["checkpoint_sha256"] = sha(checkpoint / "model.safetensors")
            if args.diagnostic_ids:
                save(
                    checkpoint / "diagnostic-only.json",
                    {"diagnostic": True, "checkpoint_sha256": report["checkpoint_sha256"]},
                )
                report["diagnostic_passed"] = diagnostic_passed(
                    report["diagnostic_snapshots"], report["changed_tensors"]
                ) and all(
                    any(s["update_norms"][p] > 0 for s in report["steps"])
                    for p in ("head.", "type_emb.", "scorer.")
                )
            report["after_fp32"] = predictions()
            report["step_median_seconds"] = statistics.median(s["seconds"] for s in report["steps"])
        if sha(base_weights) != original_hash:
            raise ValueError("Original model changed")
        report.update(status="completed", elapsed_seconds=time.perf_counter() - started)
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save(output / (args.stage + ".json"), report)
        raise
    finally:
        stopped.set()
        resident.__exit__(None, None, None)
        guard.__exit__(None, None, None)
    save(output / (args.stage + ".json"), report)
    print(json.dumps({k: report[k] for k in ["stage", "status", "elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
