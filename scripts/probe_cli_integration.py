"""Exercise the installed current-task CLI path, keeping native MCP discovery distinct."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from jev_context.common import dumps, now, uid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["ready", "stopped"], required=True)
    args = parser.parse_args()
    report = {
        "created_at": now(),
        "phase": args.phase,
        "transport": "CLI sharing the MCP Service contract",
        "native_mcp_discovery_verified": False,
        "calls": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def call(tool, **value):
        value.update(request_id=uid("req"), contract_version="2.0")
        command = [
            sys.executable,
            "-m",
            "jev_context",
            "call",
            "--config",
            str(args.config),
            "--tool",
            tool,
            "--prepare-engine",
        ]
        tick = time.monotonic()
        process = subprocess.run(
            command,
            input=dumps(value),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        elapsed = (time.monotonic() - tick) * 1000
        result = json.loads(process.stdout)
        report["calls"].append(
            {
                "command": command,
                "tool": tool,
                "exit_code": process.returncode,
                "latency_ms": elapsed,
                "stdout_bytes": len(process.stdout.encode()),
                "result": result,
            }
        )
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert process.returncode == 0, result
        assert result["contract_version"] == "2.0"
        return result["data"]

    status = call("workspace_status")
    expected = "shadow" if args.phase == "ready" else "unavailable"
    assert status["engine"]["state"] == expected, status["engine"]
    work = call("work_open", work_id=args.work_id)
    query = "OpenJev Modal 모델 한국어 품질 검증"
    parameters = {
        "work_id": args.work_id,
        "query": query,
        "budget_bytes": 32768,
        "claim": "모델의 실제 호스트 품질과 토큰 절감이 모두 검증되어 자동 선택을 켤 수 있다",
    }
    baseline = call("context_prepare", **parameters, judge_mode="off")
    observed = call("context_prepare", **parameters, judge_mode="observe")

    def identity(packet):
        return {(i["source_id"], i["revision"], i["content_hash"]) for i in packet["evidence"]}

    assert identity(baseline)
    missing = identity(baseline) - identity(observed)
    required = {
        key
        for item in baseline["evidence"]
        if item.get("role") == "required_evidence"
        for key in [(item["source_id"], item["revision"], item["content_hash"])]
    }
    assert required <= identity(observed)
    report["baseline_evidence_count"] = len(baseline["evidence"])
    report["budget_excluded_count"] = len(missing)
    report["same_evidence_in_packet"] = not missing
    if missing:
        assert observed["wire_budget"].get("omitted_optional", 0) > 0
        for item in baseline["evidence"]:
            key = (item["source_id"], item["revision"], item["content_hash"])
            if key in missing:
                original = call(
                    "source_read",
                    source_id=item["source_id"],
                    revision=item["revision"],
                    start_line=item["start_line"],
                    max_bytes=16384,
                )
                assert item["text"] in original["text"]
    report["excluded_originals_retrieved"] = True
    expected_judgment = "observed" if args.phase == "ready" else "abstained"
    assert observed["judgment"]["status"] == expected_judgment, observed["judgment"]
    assert observed["scope"]["constraints"] == work["scope"]["constraints"]
    if args.phase == "ready":
        details = call(
            "work_inspect", work_id=args.work_id, view="judgments", packet_id=observed["packet_id"]
        )
        report["judgment_details"] = details
    report.update(
        required_evidence_preserved=True,
        constraints_preserved=True,
        evidence_count=len(observed["evidence"]),
        judgment_status=expected_judgment,
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(dumps({k: v for k, v in report.items() if k not in {"calls", "judgment_details"}}))


if __name__ == "__main__":
    main()
