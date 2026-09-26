"""Measure existing OpenVINO assets in a fresh dedicated Python; never download a model."""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--managed-cache", action="store_true")
    parser.add_argument("--cache-mode", choices=["OPTIMIZE_SIZE", "OPTIMIZE_SPEED"])
    parser.add_argument("--disable-mmap", action="store_true")
    parser.add_argument("--disable-onednn", action="store_true")
    parser.add_argument("--force-implementations", type=Path)
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--cases", type=Path, default=ROOT / "models/korean-diagnostic.json")
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    if args.disable_onednn:
        os.environ["OV_GPU_USE_ONEDNN"] = "0"
    if args.force_implementations:
        os.environ["OV_GPU_FORCE_IMPLEMENTATIONS"] = args.force_implementations.read_text(
            encoding="utf-8"
        ).strip()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    model_path = Path(profile["model_path"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"profile": profile, "pid": os.getpid(), "stages_seconds": {}, "results": []}
    report["started_at"] = datetime.now(UTC).isoformat()
    report["command"] = sys.argv
    report["dataset_sha256"] = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    report["gpu_use_onednn_override"] = os.environ.get("OV_GPU_USE_ONEDNN")
    report["gpu_force_implementations"] = os.environ.get("OV_GPU_FORCE_IMPLEMENTATIONS")
    started = time.perf_counter()

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def stage(name, action):
        tick = time.perf_counter()
        value = action()
        report["stages_seconds"][name] = time.perf_counter() - tick
        save()
        print(json.dumps({name: report["stages_seconds"][name]}), flush=True)
        return value

    from jev_context.judgment import question
    from jev_context.semif_worker import answer, prompt_ids
    from jev_context.storage import FileLock

    def verify():
        raw = (model_path / "manifest.json").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == profile["manifest_sha256"]
        for relative, expected in json.loads(raw)["files"].items():
            path = (model_path / relative).resolve(strict=True)
            assert path.is_relative_to(model_path.resolve())
            with path.open("rb") as stream:
                assert hashlib.file_digest(stream, "sha256").hexdigest() == expected

    try:
        with FileLock(Path(profile["lock_root"]) / "resident.lock"):
            stage("manifest_verify", verify)
            ov = stage("openvino_import", lambda: __import__("openvino"))
            transformers = stage("transformers_import", lambda: __import__("transformers"))
            tokenizer = stage(
                "tokenizer_load",
                lambda: transformers.AutoTokenizer.from_pretrained(
                    model_path, local_files_only=True
                ),
            )
            static = json.loads((model_path / "static.json").read_text(encoding="utf-8"))
            core = stage("core_create", ov.Core)
            if args.disable_mmap:
                core.set_property({"ENABLE_MMAP": False})
            report["enable_mmap"] = not args.disable_mmap
            report["runtime_version"] = ov.__version__
            report["device"] = str(core.get_property(profile["device"], "FULL_DEVICE_NAME"))
            compile_options = {"PERFORMANCE_HINT": "LATENCY"}
            if args.cache_mode:
                compile_options["CACHE_MODE"] = args.cache_mode
            report["compile_options"] = compile_options
            if args.managed_cache and not args.cache:
                raise ValueError("--managed-cache requires --cache")
            if args.cache:
                args.cache = args.cache.resolve()
                if args.managed_cache:
                    from jev_context.openvino_cache import cache_namespace, prepare_cache

                    args.cache, identity = cache_namespace(
                        args.cache, model_path, ov, core, profile["device"], compile_options
                    )
                    prepare_cache(args.cache, identity)
                    report["cache_identity"] = identity
                else:
                    args.cache.mkdir(parents=True, exist_ok=True)
                core.set_property({"CACHE_DIR": str(args.cache)})
            report["cache_dir"] = str(args.cache) if args.cache else None
            compiled = stage(
                "compile",
                lambda: core.compile_model(
                    model_path / "model.xml", profile["device"], compile_options
                ),
            )
            report["loaded_from_cache"] = bool(compiled.get_property("LOADED_FROM_CACHE"))
            save()
            if args.compile_only:
                operations = []
                for operation in compiled.get_runtime_model().get_ordered_ops():
                    info = operation.get_rt_info()
                    operations.append(
                        {
                            "name": operation.get_friendly_name(),
                            "info": {key: str(value.value) for key, value in info.items()},
                        }
                    )
                report["runtime_operations"] = operations
                report["compile_only"] = True
                return
            infer = stage("infer_request", lambda: compiled.create_infer_request().infer)
            warm = prompt_ids(
                tokenizer,
                {"text": "준비 확인"},
                {"instructions": "Kor?"},
                [["yes", "Korean"], ["no", "other"]],
            )
            stage("warmup", lambda: answer(infer, static, *warm, ["yes", "no"]))
            stage("question_timing", lambda: answer(infer, static, *warm, ["yes", "no"]))
            report["ready_seconds"] = time.perf_counter() - started
            for case in json.loads(args.cases.read_text(encoding="utf-8"))["cases"]:
                q = question(case["purpose"], case["language"])
                ids, slots = prompt_ids(tokenizer, case["state"], q, list(q["options"].items()))
                tick = time.perf_counter()
                result = answer(infer, static, ids, slots, list(q["options"]))
                report["results"].append(
                    {
                        "id": case["id"],
                        "input_tokens": len(ids),
                        "seconds": time.perf_counter() - tick,
                        **result,
                    }
                )
            report["passed"] = True
    except Exception as exc:
        report.update(passed=False, error=repr(exc))
        raise
    finally:
        report["total_seconds"] = time.perf_counter() - started
        save()
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"profile", "results"}}, ensure_ascii=False
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
