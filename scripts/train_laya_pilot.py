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
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def validate_run(args, cases):
    steps = getattr(args, "steps", 100)
    rate = getattr(args, "learning_rate", 1e-5)
    if (steps, rate) not in {(100, 1e-5), (300, 1e-5), (300, 3e-5)}:
        raise ValueError("Only approved A/B/C training settings are allowed")
    selection = getattr(args, "diagnostic_ids", None)
    if not selection:
        return cases
    if args.data_profile == "public-pilot" or rate != 1e-5 or steps != 300:
        raise ValueError("Diagnostic requires frozen train, 300 steps and learning rate 1e-5")
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
    parser.add_argument("stage", choices=["train", "reload"])
    parser.add_argument("--base", type=Path, default=ROOT / ".local/models/laya-multilingual")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/laya-finetuning/pilot")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--diagnostic-ids", type=Path)
    parser.add_argument(
        "--split", choices=["train", "development", "calibration", "test"], required=True
    )
    parser.add_argument(
        "--data-profile", choices=["actual", "synthetic-experiment", "public-pilot"], required=True
    )
    args = parser.parse_args()
    cases, questions = training_inputs(args)
    validate_run(args, cases)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from laya import Agent
    from laya.common import build_sequence, collate_items, render_options, serialize_state

    from jev_context.storage import FileLock

    torch.set_num_threads(4)
    torch.manual_seed(20260928)
    random.seed(20260928)
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
        "seed": 20260928,
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
                now - started > 1200
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
        if args.stage == "train":
            model_path = output / "reference-base"
            shutil.copytree(args.base, model_path)
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
            optimizer = torch.optim.AdamW(trainable, lr=args.learning_rate)
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
