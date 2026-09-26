"""Run ONLY with an explicitly supplied, separately prepared local Laya environment."""

import contextlib
import json
import os
import sys
from pathlib import Path


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    model_path, device = Path(sys.argv[1]).resolve(strict=True), sys.argv[2]
    if not all(
        (model_path / name).exists()
        for name in ("rl_agent_config.json", "model.safetensors", "tokenizer", "encoder")
    ):
        raise ValueError("An offline complete Laya checkpoint is required")
    # Third-party warnings must never corrupt the JSON protocol.
    with contextlib.redirect_stdout(sys.stderr):
        if os.environ.get("JEV_CPU_THREADS"):
            import torch

            torch.set_num_threads(int(os.environ["JEV_CPU_THREADS"]))
        import laya
        from laya.common import render_options, serialize_state

        agent = laya.load(str(model_path), device=device)
        if os.environ.get("JEV_WARMUP") == "1":
            agent.predict(
                {"text": "준비 확인"},
                {
                    "warmup": {
                        "type": "choice",
                        "instructions": "Is the text Korean?",
                        "criteria": {"yes": "Korean", "no": "other"},
                    }
                },
            )
    initial_device = str(agent.device)
    if initial_device != device:
        raise ValueError("Requested device is unavailable; profile must be reviewed")
    print(json.dumps({"status": "ready", "device": initial_device}), flush=True)
    while True:
        line = sys.stdin.buffer.readline(65537)
        if not line:
            break
        if len(line) > 65536:
            break
        request = json.loads(line)
        try:
            # Replicate pre-truncation lengths from pinned laya/common.py.
            # No instruction, option, or state clipping is accepted.
            for question in request["questions"].values():
                q = agent._to_internal(question)
                tok = agent.tok
                if tok.mask_token and tok.mask_token in serialize_state(request["state"]):
                    raise ValueError("State contains tokenizer mask token")
                head = len(
                    tok(f"{q['t']} question: {q['ins']}", add_special_tokens=False)["input_ids"]
                )
                lengths = [
                    len(tok(" " + option, add_special_tokens=False)["input_ids"])
                    for option in render_options(q)
                ]
                if any(length > 48 for length in lengths):
                    raise ValueError("Option would be truncated")
                options = sum(1 + length for length in lengths)
                state = len(
                    tok(serialize_state(request["state"]), add_special_tokens=False)["input_ids"]
                )
                if max(head, 16) + options > agent.cfg.get(
                    "head_max_len", 192
                ) or head + options + state + 4 > agent.cfg.get("max_len", 512):
                    raise ValueError("Input would be truncated")
            with contextlib.redirect_stdout(sys.stderr):
                result = agent.predict(request["state"], request["questions"])
            if str(agent.device) != initial_device:
                raise ValueError("Device changed; resource profile invalid")
            result["evaluation_id"] = request["evaluation_id"]
        except Exception:
            result = {
                "evaluation_id": request.get("evaluation_id"),
                "error": "input_or_runtime_incomplete",
            }
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    # The worker owns the model lock. If the host dies during inference, a new host
    # cannot load a duplicate model while the old worker is still alive.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from jev_context.common import DomainError
    from jev_context.storage import FileLock
    from jev_context.worker_lifetime import bind_owner

    try:
        bind_owner()
        with FileLock(Path(sys.argv[3])):
            main()
    except DomainError:
        print('{"status":"busy"}', flush=True)
    except Exception as exc:
        print(json.dumps({"status": "error", "reason": type(exc).__name__}), flush=True)
