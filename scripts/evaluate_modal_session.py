"""Actual Windows -> private Modal session -> GPU and MCP evaluation, without promotion."""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jev_context.common import DomainError, digest, dumps, now, uid
from jev_context.judgment import question
from jev_context.modal_engine import ModalOpenJev
from jev_context.policy import Config

ROOT = Path(__file__).resolve().parents[1]


async def run(args):
    config = Config.load(args.config)
    profile = json.loads(Path(config.engine["profile_file"]).read_text(encoding="utf-8"))
    engine = ModalOpenJev(profile)
    engine.prepare()
    if engine.state != "shadow":
        raise RuntimeError("Start the matching work session and wait until ready")
    raw = (ROOT / "models/openjev-challenge.json").read_bytes()
    dataset = json.loads(raw)
    report = {
        "created_at": now(),
        "profile_fingerprint": engine.fingerprint,
        "dataset_sha256": digest(raw),
        "dataset_provenance": dataset["provenance"],
        "split": dataset["split"],
        "promotion_eligible": False,
        "desktop_current_session_verified": False,
        "latency_scope": "Windows client, local bridge, Modal queues, tokenizer and GPU",
        "results": [],
        "mcp_calls": [],
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def request(case):
        return {
            "evaluation_id": uid("eval"),
            "profile_fingerprint": engine.fingerprint,
            "project_id": config.project_id,
            "work_id": args.work_id,
            "deadline_ms": 2000,
            "state": case["state"],
            "questions": [question(case["purpose"], case["language"])],
        }

    # Paired single-candidate requests separate language quality from batching effects.
    for case in dataset["cases"]:
        started = time.monotonic()
        try:
            result = engine.evaluate(request(case))
        except DomainError as exc:
            result = {"status": "abstained", "reason": exc.code}
        elapsed = (time.monotonic() - started) * 1000
        answer = next(iter(result.get("answers", [])), {})
        row = {k: case[k] for k in ("id", "language", "purpose", "expected", "critical", "tag")}
        row.update(
            result=result, correct=answer.get("choice") == case["expected"], latency_ms=elapsed
        )
        report["results"].append(row)
        save()
        print(
            dumps(
                {
                    "id": case["id"],
                    "correct": row["correct"],
                    "status": result["status"],
                    "choice": answer.get("choice"),
                    "ms": round(elapsed),
                }
            ),
            flush=True,
        )
        if result.get("reason") in {"deadline_exceeded", "engine_unavailable"}:
            report["interrupted"] = result["reason"]
            break
    for language in ("ko", "en"):
        rows = [r for r in report["results"] if r["language"] == language]
        times = sorted(r["latency_ms"] for r in rows)
        report[language] = {
            "count": len(rows),
            "correct": sum(r["correct"] for r in rows),
            "critical_errors": sum(r["critical"] and not r["correct"] for r in rows),
            "abstentions": sum(r["result"]["status"] != "observed" for r in rows),
            "p50_ms": statistics.median(times) if times else None,
            "p95_ms": times[min(len(times) - 1, int(len(times) * 0.95))] if times else None,
        }
    save()
    if report.get("interrupted"):
        return

    # Eight independent states in one RPC; no concatenation into a new prompt.
    started = time.monotonic()
    try:
        results = engine.evaluate_many([request(c) for c in dataset["cases"][:8]])
        report["batch8"] = {"results": results, "latency_ms": (time.monotonic() - started) * 1000}
    except DomainError as exc:
        report["batch8"] = {"error": exc.code}
    save()

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "jev_context",
            "serve",
            "--config",
            str(args.config),
            "--prepare-engine",
        ],
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        listing = await session.list_tools()
        report["mcp_remote_annotations"] = {
            tool.name: tool.annotations.openWorldHint for tool in listing.tools
        }

        async def call(name, **arguments):
            arguments.update(contract_version="2.0", request_id=uid("req"))
            if name in {"source_sync", "work_record"}:
                arguments["mutation_id"] = uid("mut")
            started = time.monotonic()
            result = await session.call_tool(name, arguments)
            elapsed = (time.monotonic() - started) * 1000
            report["mcp_calls"].append(
                {"tool": name, "latency_ms": elapsed, "result": result.structuredContent}
            )
            save()
            assert not result.isError, result.structuredContent
            return result.structuredContent

        await call("workspace_status")
        work = (await call("work_open", work_id=args.work_id))["data"]
        # Real project evidence, uploaded only as selected candidate excerpts.
        sources = await call(
            "source_sync",
            items=[
                {"kind": "file", "relative_path": "docs/implementation-v2.md"},
                {"kind": "file", "relative_path": "docs/openjev-modal-evaluation.md"},
                {"kind": "file", "relative_path": "docs/translation-evaluation.md"},
            ],
        )
        assert all("source_id" in s for s in sources["data"]["items"]), sources
        context_args = {
            "work_id": args.work_id,
            "query": "OpenJev Modal 모델 판단 한국어 평가",
            "claim": "OpenJev의 실제 호스트 문맥 효율과 한국어 품질이 모두 검증되어 자동 선택을 활성화할 수 있다",
            "budget_bytes": 32768,
        }
        baseline = await call("context_prepare", **context_args, judge_mode="off")
        shadow = await call("context_prepare", **context_args, judge_mode="observe")

        def refs(packet):
            return {
                (e["source_id"], e["revision"], e["content_hash"])
                for e in packet["data"]["evidence"]
            }

        report["shadow_evidence_preserved"] = refs(baseline) <= refs(shadow)
        report["wire_comparison"] = {
            "baseline_bytes": len(dumps(baseline).encode()),
            "shadow_bytes": len(dumps(shadow).encode()),
            "scope": "Structured payload bytes, not host input tokens or task efficiency",
        }
        await call(
            "capability_recommend",
            work_id=args.work_id,
            query="읽기만으로 모델 검증 문서의 상충점을 조사",
            inventory={
                "revision": "integration-1",
                "observed_at": now(),
                "complete": False,
                "items": [
                    {
                        "id": "source_read",
                        "kind": "tool",
                        "description": "고정 revision의 원문 읽기",
                        "version": "2.0",
                        "available": True,
                        "mandatory": True,
                    },
                    {
                        "id": "source_sync",
                        "kind": "tool",
                        "description": "프로젝트 허용 문서를 색인에 등록",
                        "version": "2.0",
                        "available": True,
                        "mandatory": False,
                    },
                ],
            },
        )
        await call(
            "handoff_prepare",
            work_id=args.work_id,
            expected_work_revision=work["revision"],
            query="OpenJev 한국어 평가",
            role="문서 검토",
            round=0,
            mode="read",
            budget_bytes=32768,
        )

    # Oversized token input must abstain without silently clipping the Korean original.
    oversized = request(dataset["cases"][0])
    oversized["state"] = {"candidate": "한국어 원문은 축약 없이 보존한다. " * 2000}
    try:
        report["oversized"] = engine.evaluate(oversized)
    except DomainError as exc:
        report["oversized"] = {"status": "abstained", "reason": exc.code}
    save()
    print(
        dumps({k: report[k] for k in ("ko", "en", "shadow_evidence_preserved", "wire_comparison")}),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    anyio.run(run, args)
