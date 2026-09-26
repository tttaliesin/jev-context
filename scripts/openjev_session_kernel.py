"""Bounded JSONL kernel inside /opt/venv; only the owning Modal function can reach it."""

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from worker import backend_command, wait_ready

MAX_FRAME = 262144
ENGINE_SHA = "4615880c869dc549fd27f57d1eecc20d2637f538b5337bfc61c620764fb5ed83"


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


async def run(model_path, max_input_tokens):
    import openjev.engine
    from openjev.config import Settings
    from openjev.engine import Engine
    from transformers import AutoTokenizer

    if hashlib.sha256(Path(openjev.engine.__file__).read_bytes()).hexdigest() != ENGINE_SHA:
        raise RuntimeError("OpenJev implementation changed")
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)

    class CheckedEngine(Engine):
        async def _post(self, path, body):
            if path != "/v1/chat/completions":
                raise ValueError("unsupported_model_request")
            # Use the same vLLM renderer as inference, not a separately reconstructed template.
            tokenized = await super()._post(
                "/tokenize",
                {
                    "model": body["model"],
                    "messages": body["messages"],
                    "chat_template_kwargs": body["chat_template_kwargs"],
                    "add_generation_prompt": True,
                },
            )
            count = tokenized["count"]
            if type(count) is not int or not 1 <= count <= max_input_tokens:
                raise ValueError("input_incomplete")
            canvas = body["vllm_xargs"]["diffusion_canvas_length"]
            if count + max(canvas, body["max_tokens"]) > tokenized["max_model_len"]:
                raise ValueError("input_incomplete")
            result = await super()._post(path, body)
            actual = result["usage"]["prompt_tokens"]
            if actual != count:
                raise ValueError("token_count_mismatch")
            self.verified_counts.append(count)
            return result

    async def evaluate(item):
        engine = CheckedEngine(Settings(tokenizer=model_path, max_inflight=8), tokenizer)
        engine.verified_counts = []
        started = time.monotonic()
        try:
            seed = int.from_bytes(
                hashlib.sha256(
                    json.dumps([item["state"], item["questions"]], sort_keys=True).encode()
                ).digest()[:4],
                "big",
            )
            answers, tokens, thought = await engine.decide(
                item["questions"],
                item["state"],
                seed,
                options={"steps": 1, "think": 0, "sequential": False},
            )
            return {
                "evaluation_id": item["evaluation_id"],
                "answers": answers,
                "usage": {
                    "input_tokens": tokens,
                    "output_tokens": thought,
                    "verified_read_tokens": engine.verified_counts,
                    "processed_input_tokens": sum(engine.verified_counts),
                    "token_count_verified": bool(engine.verified_counts),
                    "model_ms": (time.monotonic() - started) * 1000,
                },
            }
        except ValueError as exc:
            code = str(exc)
            return {
                "evaluation_id": item["evaluation_id"],
                "error": code
                if code in {"input_incomplete", "token_count_mismatch"}
                else "engine_invalid_response",
            }
        finally:
            await engine.close()

    warmup = await evaluate(
        {
            "evaluation_id": "warmup",
            "state": "The sky is blue.",
            "questions": {
                "color": {
                    "type": "choice",
                    "instructions": "What color is the sky?",
                    "criteria": {"blue": "blue", "red": "red"},
                },
            },
        }
    )
    if warmup.get("error"):
        emit({"status": "failed", "error": warmup["error"]})
        return
    emit({"status": "ready", "engine_sha256": ENGINE_SHA, "warmup": warmup["usage"]})
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME + 1)
        if not line:
            return
        if len(line) > MAX_FRAME:
            raise ValueError("oversized_frame")
        batch = json.loads(line)
        remaining = min(2, batch["deadline_at"] - time.time())
        if remaining <= 0:
            emit({"batch_id": batch["batch_id"], "error": "deadline_exceeded"})
            return
        try:
            async with asyncio.timeout(remaining):
                rows = await asyncio.gather(*(evaluate(item) for item in batch["items"]))
            emit({"batch_id": batch["batch_id"], "items": rows})
        except TimeoutError:
            emit({"batch_id": batch["batch_id"], "error": "deadline_exceeded"})
            return  # Exit kills the GPU backend; timed-out requests cannot continue running.


def main():
    model_path, max_input_tokens = sys.argv[1], int(sys.argv[2])
    with Path("/tmp/openjev-session.log").open("w") as logs:
        backend = subprocess.Popen(
            backend_command(model_path), stdout=logs, stderr=logs, start_new_session=True
        )
        try:
            wait_ready(backend, "http://127.0.0.1:8000/health", time.monotonic() + 300)
            asyncio.run(run(model_path, max_input_tokens))
        finally:
            if backend.poll() is None:
                os.killpg(backend.pid, signal.SIGTERM)
                try:
                    backend.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(backend.pid, signal.SIGKILL)
                    backend.wait(timeout=5)


if __name__ == "__main__":
    main()
