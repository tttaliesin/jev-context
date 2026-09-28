"""Bounded CPU head-training smoke test; exposed development data, never a quality gate."""

import argparse
import ctypes
import hashlib
import json
import os
import random
import shutil
import statistics
import struct
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from laya import Agent
    from laya.common import build_sequence, collate_items, render_options

    torch.set_num_threads(4)
    torch.manual_seed(20260928)
    random.seed(20260928)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    with (output / (args.stage + ".started")).open("x") as marker:
        marker.write(str(time.time()))
    report = {"stage": args.stage, "quality_evaluation": False, "promotion_eligible": False}
    started = time.perf_counter()
    try:
        if memory_gib() < 4:
            raise MemoryError("Require 4 GiB available before load")
        base_weights = args.base / "model.safetensors"
        original_hash = sha(base_weights)
        questions = read(ROOT / "evaluations/ollaya-tuning/candidates.json")["a_original"][
            "questions"
        ]
        cases = read(ROOT / "evaluations/ollaya-tuning/development.json")["cases"]
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
            optimizer = torch.optim.AdamW(trainable, lr=1e-5)
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
            order = list(range(len(prepared)))
            for step in range(100):
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
                torch.nn.utils.clip_grad_norm_(trainable, 1.0, error_if_nonfinite=True)
                optimizer.step()
                elapsed = time.perf_counter() - began
                report["steps"].append({"step": step + 1, "loss": loss.item(), "seconds": elapsed})
                save(output / "train.json", report)
                print(
                    f"step {step + 1}/100 loss={loss.item():.4f} seconds={elapsed:.3f}", flush=True
                )
                if elapsed > 30:
                    raise TimeoutError("Step exceeded 30 seconds")
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
            report["after_fp32"] = predictions()
            report["step_median_seconds"] = statistics.median(s["seconds"] for s in report["steps"])
        if sha(base_weights) != original_hash:
            raise ValueError("Original model changed")
        report.update(status="completed", elapsed_seconds=time.perf_counter() - started)
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save(output / (args.stage + ".json"), report)
        raise
    save(output / (args.stage + ".json"), report)
    print(json.dumps({k: report[k] for k in ["stage", "status", "elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
