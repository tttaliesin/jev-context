"""Linux-only bounded worker inside the pinned OpenJev image."""

import asyncio
import hashlib
import importlib.metadata
import json
import os
import signal
import statistics
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def http(path, body=None, timeout=30):
    request = Request(
        "http://127.0.0.1:8080" + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def wait_ready(process, url, deadline):
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Server exited with {process.returncode}")
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(2)
    raise TimeoutError("Model preparation exceeded startup deadline")


def backend_command(model_path):
    return [
        "/opt/venv/bin/vllm",
        "serve",
        model_path,
        "--served-model-name",
        "dgemma",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--diffusion-config",
        '{"canvas_length":64}',
        "--max-logprobs",
        "32",
        "--limit-mm-per-prompt",
        '{"image":0,"video":0}',
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "gemma4",
        "--reasoning-parser",
        "gemma4",
        "--override-generation-config",
        '{"max_new_tokens":null}',
        "--enable-prefix-caching",
        "--async-scheduling",
        "--attention-backend",
        "TRITON_ATTN",
        "--max-num-seqs",
        "8",
        "--max-model-len",
        "8192",
        "--gpu-memory-utilization",
        "0.8",
        "--enforce-eager",
    ]


def main():
    model_path = sys.argv[1]
    started = time.monotonic()
    deadline = started + 1140
    processes, results = [], []
    report = {"status": "starting", "created_at": datetime.now(UTC).isoformat()}
    log_path = Path("/tmp/openjev-server.log")
    try:
        report["gpu"] = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            text=True,
        ).strip()
        report["packages"] = {
            p: importlib.metadata.version(p)
            for p in (
                "openjev",
                "vllm",
                "torch",
                "transformers",
                "flashinfer-python",
                "flashinfer-jit-cache",
            )
        }
        vllm = backend_command(model_path)
        report["vllm_arguments"] = vllm[3:]
        with log_path.open("w") as logs:
            backend = subprocess.Popen(vllm, stdout=logs, stderr=logs, start_new_session=True)
            processes.append(backend)
            wait_ready(backend, "http://127.0.0.1:8000/health", min(deadline, started + 900))
            api = subprocess.Popen(
                ["/opt/venv/bin/python", "-m", "openjev"],
                env={
                    **os.environ,
                    "OPENJEV_TOKENIZER": model_path,
                    "OPENJEV_HOST": "127.0.0.1",
                    "OPENJEV_PORT": "8080",
                    "OPENJEV_MAX_INFLIGHT": "8",
                },
                stdout=logs,
                stderr=logs,
                start_new_session=True,
            )
            processes.append(api)
            wait_ready(api, "http://127.0.0.1:8080/health", min(deadline, time.monotonic() + 90))
            from openjev.config import Settings
            from openjev.engine import Engine
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
            counter = Engine(Settings(tokenizer=model_path), tokenizer)
            report["engine_source_sha256"] = hashlib.sha256(
                Path(sys.modules["openjev.engine"].__file__).read_bytes()
            ).hexdigest()
            # Separate warmup from measured fixtures; use the same narrow API shape.
            http(
                "/v1/systemone",
                {
                    "model": "openjev-0.1",
                    "state": "The sky is blue.",
                    "questions": {
                        "warmup": {
                            "type": "choice",
                            "instructions": "What color is the sky?",
                            "criteria": {"blue": "blue", "red": "red"},
                        }
                    },
                },
                timeout=60,
            )
            report["preparation_seconds"] = time.monotonic() - started
            for case in json.loads(Path("/tmp/cases.json").read_text())["cases"]:
                if time.monotonic() + 35 > deadline:
                    raise TimeoutError("Total diagnostic time limit reached")
                q = case["question"]
                questions = {
                    q["id"]: {
                        "type": "choice",
                        "instructions": q["instructions"],
                        "criteria": q["options"],
                    }
                }
                schema = counter.build_schema(questions)
                groups = counter.groups(schema["questions"], schema["format"])
                # Exact installed packer and tokenizer, no clipping/estimated counts.
                counts = [
                    len(
                        counter.chat_prompt_ids(
                            counter.system_text(g, schema["format"], len(groups) > 1),
                            json.dumps(case["state"], ensure_ascii=False),
                        )
                    )
                    for g in groups
                ]
                if max(counts, default=0) + 65 > 8192:
                    raise ValueError("Packed request exceeds server context")
                body = {"model": "openjev-0.1", "state": case["state"], "questions": questions}
                tick = time.monotonic()
                raw = http("/v1/systemone", body)
                latency = (time.monotonic() - tick) * 1000
                answer = raw["answers"][q["id"]]
                assert answer["choice"] in q["options"]
                row = {k: case[k] for k in ("id", "purpose", "language", "expected", "critical")}
                row.update(
                    {
                        "correct": answer["choice"] == case["expected"],
                        "choice": answer["choice"],
                        "latency_ms": latency,
                        "within_2s_budget": latency <= 2000,
                        "packed_input_tokens": counts,
                        "raw_response": raw,
                    }
                )
                results.append(row)
                print(
                    json.dumps(
                        {"id": row["id"], "correct": row["correct"], "latency_ms": round(latency)}
                    ),
                    flush=True,
                )
            asyncio.run(counter.close())
            report["status"] = "completed"
    except Exception as exc:
        report.update({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)})
        traceback.print_exc()
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        report["results"] = results
        report["total_seconds"] = time.monotonic() - started
        report["summary"] = {}
        for language in ("ko", "en"):
            rows = [r for r in results if r["language"] == language]
            times = sorted(r["latency_ms"] for r in rows)
            report["summary"][language] = {
                "count": len(rows),
                "correct": sum(r["correct"] for r in rows),
                "critical_errors": sum(r["critical"] and not r["correct"] for r in rows),
                "within_2s": sum(r["within_2s_budget"] for r in rows),
                "latency_p50_ms": statistics.median(times) if times else None,
                "latency_p95_ms": times[min(len(times) - 1, int(len(times) * 0.95))]
                if times
                else None,
            }
        report["server_log_tail"] = (
            log_path.read_text(errors="replace")[-20000:] if log_path.exists() else ""
        )
        Path("/tmp/report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
