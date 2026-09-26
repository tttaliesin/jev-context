"""Run ONLY with an explicitly supplied, separately prepared OpenVINO environment.

Direct option readout after SemIf (github.com/TheoLeeCJ/SemIf, MIT, commit 23cf1f39): state,
criterion and lettered options go into one chat turn, and a single forward pass reads the
next-token logits of the option letters; nothing is generated. The model directory holds a
fixed-length OpenVINO IR (right-padded input_ids plus last_index) and its tokenizer.
Inputs longer than that length are rejected, never truncated.
"""

import contextlib
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

LETTERS = "ABCDEFGHIJKLMNOP"
DIRECT_SYSTEM = (
    "Apply the supplied criterion to the supplied evidence. Choose exactly one listed option. "
    "Respond with only its uppercase letter, with no explanation or reasoning."
)


def prompt_ids(tokenizer, state, question, options):
    """Tokenized chat prompt plus the single-token answer slot of each option letter.

    options is an ordered list of (label, description); letters follow that order.
    """
    payload = {
        "evidence": state,
        "criterion": question["instructions"],
        "options": [
            {"letter": LETTERS[i], "description": description}
            for i, (_, description) in enumerate(options)
        ],
    }
    messages = [
        {"role": "system", "content": DIRECT_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    ids = tokenizer.encode(prompt, add_special_tokens=False)
    slots = []
    for letter in LETTERS[: len(options)]:
        encoded = tokenizer.encode(letter, add_special_tokens=False)
        if len(encoded) != 1 or tokenizer.decode(encoded) != letter:
            raise ValueError("Answer slot is not one round-trip token")
        if tokenizer.encode(prompt + letter, add_special_tokens=False) != ids + encoded:
            raise ValueError("Answer boundary changes tokenization")
        slots.append(encoded[0])
    return ids, slots


def answer(request_infer, static, ids, slots, labels):
    import numpy as np

    if not ids or len(ids) > static["length"]:
        raise ValueError("Input exceeds the fixed model length")
    padded = ids + [static["pad_id"]] * (static["length"] - len(ids))
    logits = request_infer(
        {
            "input_ids": np.array([padded], dtype=np.int64),
            "last_index": np.array([len(ids) - 1], dtype=np.int64),
        }
    )[0][0]
    values = [float(logits[slot]) for slot in slots]
    top = max(values)
    weights = [math.exp(v - top) for v in values]
    total = sum(weights)
    probabilities = {label: w / total for label, w in zip(labels, weights, strict=True)}
    entropy = -sum(p * math.log(p) for p in probabilities.values() if p > 0)
    return {
        "choice": max(probabilities, key=probabilities.get),
        "probabilities": probabilities,
        "confidence": max(0.0, min(1.0, 1 - entropy / math.log(len(labels)))),
    }


def redirect_native_stdout():
    """Keep native diagnostics off the protocol pipe for the entire worker lifetime."""
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        # A native library may use the Win32 standard handle rather than the CRT fd table.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        kernel.SetStdHandle.restype = wintypes.BOOL
        if not kernel.SetStdHandle(
            wintypes.DWORD(-11), wintypes.HANDLE(msvcrt.get_osfhandle(sys.stderr.fileno()))
        ):
            raise ctypes.WinError(ctypes.get_last_error())


def main(protocol):
    preparation_started = time.perf_counter()
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    model_path, device = Path(sys.argv[1]).resolve(strict=True), sys.argv[2]
    for name in ("model.xml", "model.bin", "static.json", "tokenizer.json"):
        if not (model_path / name).is_file():
            raise ValueError("A complete prepared OpenVINO model directory is required")
    static = json.loads((model_path / "static.json").read_text(encoding="utf-8"))
    # Third-party warnings must never corrupt the JSON protocol.
    with contextlib.redirect_stdout(sys.stderr):
        started = time.perf_counter()
        import openvino as ov
        from transformers import AutoTokenizer

        from jev_context.openvino_cache import (
            cache_namespace,
            openvino_environment,
            prepare_cache,
            quarantine_cache,
        )
        from jev_context.storage import FileLock

        startup = {
            "import_seconds": time.perf_counter() - started,
            "runtime_environment": openvino_environment(),
            "fallback": False,
            "attempts": [],
        }
        started = time.perf_counter()
        tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        warm = [["yes", "Korean"], ["no", "other"]]
        warm_ids = prompt_ids(tokenizer, {"text": "준비 확인"}, {"instructions": "Kor?"}, warm)
        startup["tokenizer_seconds"] = time.perf_counter() - started
        compile_options = {"PERFORMANCE_HINT": "LATENCY"}

        def compile_model(core, cache, attempt):
            compiled = infer = None
            stage, started = "compile", time.perf_counter()
            try:
                if cache:
                    core.set_property({"CACHE_DIR": str(cache)})
                compiled = core.compile_model(model_path / "model.xml", device, compile_options)
                attempt["compile_seconds"] = time.perf_counter() - started
                try:
                    attempt["loaded_from_cache"] = bool(compiled.get_property("LOADED_FROM_CACHE"))
                except (AttributeError, RuntimeError):
                    pass  # Older runtimes may not expose this observation.
                stage, started = "first_warmup", time.perf_counter()
                infer = compiled.create_infer_request().infer
                if os.environ.get("JEV_WARMUP") == "1":
                    answer(infer, static, *warm_ids, ["yes", "no"])
                    attempt["first_warmup_seconds"] = time.perf_counter() - started
                return infer
            except RuntimeError as exc:
                attempt[f"{stage}_seconds"] = time.perf_counter() - started
                attempt.update(
                    error_stage=stage, error_type=type(exc).__name__, error_message=str(exc)[:2000]
                )
                # Release native objects before renaming their cache on Windows.
                core = compiled = infer = None
                raise

        started = time.perf_counter()
        core = ov.Core()
        cache_root = os.environ.get("JEV_OV_CACHE")
        cache, identity = (
            cache_namespace(cache_root, model_path, ov, core, device, compile_options)
            if cache_root
            else (None, None)
        )
        startup["cache_identity_seconds"] = time.perf_counter() - started
        # Other profiles may share the same cache root. Serialize compile/rejection of this
        # namespace even when their model residency locks live in different directories.
        cache_lock = FileLock(cache.with_suffix(".lock")) if cache else contextlib.nullcontext()
        with cache_lock:
            if cache:
                prepare_cache(cache, identity)
            for attempt_number in range(2):
                attempt = {
                    "cache_dir": str(cache) if cache else None,
                    "loaded_from_cache": None,
                    "compile_seconds": 0.0,
                    "first_warmup_seconds": None,
                }
                startup["attempts"].append(attempt)
                try:
                    infer = compile_model(core, cache, attempt)
                    break
                except RuntimeError as exc:
                    if not cache or attempt_number:
                        raise
                    # A failed answer() frame retains its bound InferRequest through the
                    # traceback. Release those frames before renaming a mapped cache.
                    traceback.clear_frames(exc.__traceback__)
                    exc.__traceback__ = None
                # Exit the exception handler too: its active exception can retain native
                # objects even after compile_model() cleared its own local references.
                core = None
                startup["fallback"] = True
                startup["rejected_cache_dir"] = str(quarantine_cache(cache))
                prepare_cache(cache, identity)
                print(
                    "compile cache rejected; retrying once in a fresh managed cache",
                    file=sys.stderr,
                )
                core = ov.Core()
        startup.update(
            cache_dir=str(cache) if cache else None,
            loaded_from_cache=attempt["loaded_from_cache"],
            compile_seconds=sum(item["compile_seconds"] for item in startup["attempts"]),
            first_warmup_seconds=sum(
                item["first_warmup_seconds"] or 0.0 for item in startup["attempts"]
            ),
        )
        # A warm forward pass costs the same for any input (fixed padded length); the host uses
        # it to skip questions that cannot finish before the judgment deadline.
        question_seconds = None
        if os.environ.get("JEV_WARMUP") == "1":
            started = time.perf_counter()
            answer(infer, static, *warm_ids, ["yes", "no"])
            question_seconds = time.perf_counter() - started
        startup["second_warmup_seconds"] = question_seconds
        startup["total_seconds"] = time.perf_counter() - preparation_started
    print(json.dumps({"status": "ready", "device": device, "question_seconds": question_seconds,
                      "startup": startup}),
          file=protocol, flush=True)  # fmt: skip
    serve(protocol, tokenizer, infer, static)


def serve(protocol, tokenizer, infer, static):
    while True:
        line = sys.stdin.buffer.readline(65537)
        if not line or len(line) > 65536:
            break
        request = json.loads(line)
        try:
            result = evaluate(tokenizer, infer, static, request)
        except Exception:
            result = {
                "evaluation_id": request.get("evaluation_id"),
                "error": "input_or_runtime_incomplete",
            }
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), file=protocol, flush=True)


def evaluate(tokenizer, infer, static, request):
    answers, tokens = {}, 0
    # The host sends the state pre-serialized so its key order survives sorted JSON.
    state = json.loads(request["state_json"])
    for question_id, question in request["questions"].items():
        options = [(label, text) for label, text in question["criteria"]]
        labels = [label for label, _ in options]
        if not 2 <= len(labels) <= len(LETTERS) or len(set(labels)) != len(labels):
            raise ValueError("Unsupported or duplicate options")
        ids, slots = prompt_ids(tokenizer, state, question, options)
        with contextlib.redirect_stdout(sys.stderr):
            answers[question_id] = answer(infer, static, ids, slots, labels)
        tokens += len(ids)
    return {"evaluation_id": request["evaluation_id"], "answers": answers,
            "usage": {"input_tokens": tokens}}  # fmt: skip


if __name__ == "__main__":
    # The worker owns the model lock, like laya_worker: a new host cannot load a duplicate
    # model while an old worker is still alive.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from jev_context.common import DomainError
    from jev_context.storage import FileLock
    from jev_context.worker_lifetime import bind_owner

    # Save the original pipe before redirecting fd 1. Never restore it: native background
    # threads and buffered destructor messages must remain diagnostics even after readiness.
    with os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1) as protocol:
        try:
            redirect_native_stdout()
            bind_owner()
            with FileLock(Path(sys.argv[3])):
                main(protocol)
        except DomainError:
            print('{"status":"busy"}', file=protocol, flush=True)
        except Exception as exc:
            print(
                json.dumps({"status": "error", "reason": type(exc).__name__}),
                file=protocol,
                flush=True,
            )
