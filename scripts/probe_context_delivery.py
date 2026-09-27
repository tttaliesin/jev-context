"""Compare a captured context with two fresh MCP processes; never starts a model."""

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jev_context.common import dumps, now, uid
from jev_context.policy import Config


def state_hash(config, wid):
    with sqlite3.connect(config.db_path.as_uri() + "?mode=ro", uri=True) as db:
        state = {"work": db.execute("SELECT body FROM works WHERE id=?", (wid,)).fetchone()[0]}
        for table in ("events", "evidence", "issues", "decisions"):
            state[table] = db.execute(
                f"SELECT id,body FROM {table} WHERE work_id=? ORDER BY id", (wid,)
            ).fetchall()
    return hashlib.sha256(dumps(state).encode()).hexdigest()


async def run(args):
    config = Config.load(args.config)
    baseline = json.loads(args.before.read_text(encoding="utf-8"))
    original_hash = state_hash(config, args.work_id)
    report = {
        "observed_at": now(),
        "transport": "MCP Python SDK / two fresh stdio processes",
        "model_inference": False,
        "native_host_server_reloaded": False,
        "before_state_hash": original_hash,
        "before": baseline,
        "runs": [],
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jev_context", "serve", "--config", str(args.config.resolve())],
    )
    for _ in range(2):
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()

            async def call(name, **fields):
                value = await session.call_tool(
                    name, {"contract_version": "2.0", "request_id": uid("req"), **fields}
                )
                assert not value.isError, value
                assert json.loads(value.content[0].text) == value.structuredContent
                return value.structuredContent

            async def inspect(view):
                items, cursor = [], None
                while True:
                    fields = {"cursor": cursor} if cursor else {}
                    page = (await call("work_inspect", work_id=args.work_id, view=view, **fields))[
                        "data"
                    ]
                    items.extend(page["items"])
                    cursor = page["next_cursor"]
                    if not cursor:
                        return items

            work = (await call("work_open", work_id=args.work_id))["data"]
            packet = await call(
                "context_prepare",
                work_id=args.work_id,
                expected_work_revision=baseline["data"]["work_revision"],
                source_ids=[args.source_id],
                query=args.query,
                judge_mode="off",
                budget_bytes=baseline["data"]["budget"]["limit_bytes"],
            )
            criteria, evidence = await inspect("criteria"), await inspect("evidence")
            data = packet["data"]
            assert work["revision"] == baseline["data"]["work_revision"]
            assert data["scope"]["constraints"] == baseline["data"]["scope"]["constraints"]
            assert data["scope"]["goal"] == baseline["data"]["scope"]["goal"]
            assert work["progress"]["next_actions"] == []
            assert work["checkpoint"]["status"] == "completion_reported"
            assert len(criteria) == len(
                [p for p in data["protected"] if p.get("role") == "completion_criterion"]
            )
            protected_claims = {p.get("claim") for p in data["protected"]}
            assert all(
                e["claim"] in protected_claims
                for e in evidence
                if e.get("role") in {"failure", "counterevidence"}
            )
            hashes = {e["source_hash"] for e in baseline["data"]["evidence"]}
            assert hashes and hashes == {e["source_hash"] for e in data["evidence"]}
            assert data["evidence"]
            report["runs"].append(
                {"work": work, "context": packet, "criteria": criteria, "evidence": evidence}
            )
    report["after_state_hash"] = state_hash(config, args.work_id)
    assert original_hash == report["after_state_hash"]
    first, second = [r["context"]["data"] for r in report["runs"]]
    assert first["scope"] == second["scope"]
    assert first["evidence"] == second["evidence"]
    report["summary"] = {
        "work_revision": first["work_revision"],
        "before_evidence": len(baseline["data"]["evidence"]),
        "after_evidence": len(first["evidence"]),
        "before_wire_bytes": baseline["data"]["wire_budget"]["used_bytes"],
        "after_wire_bytes": first["wire_budget"]["used_bytes"],
        "before_outcome": baseline["outcome"],
        "after_outcome": report["runs"][0]["context"]["outcome"],
        "original_records_unchanged": True,
        "restart_restored": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--output", type=Path, required=True)
    anyio.run(run, parser.parse_args())
