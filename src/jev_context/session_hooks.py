"""Codex SessionStart/UserPromptSubmit hooks: the design 2.0 refresh signal, nothing more.

Hook output enters the developer context, so it carries only fixed guidance, counts and work IDs
verified in the local store: never source text, work titles or goals, model judgments or paths.
The store is opened read-only and MCP readiness is never assumed. Any failure prints nothing.
Run as ``python -m jev_context.session_hooks --config PROJECT.toml``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time

from .policy import Config
from .sources import stale_file_sources

MAX_WORKS = 3
SEEN_LIMIT = 200
MAX_SOURCES = 2000


def open_store(config):
    if not config.db_path.exists():
        return None
    db = sqlite3.connect(f"{config.db_path.as_uri()}?mode=ro", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    meta = db.execute("SELECT project_id, root FROM project_meta").fetchone()
    if (
        not meta
        or meta["project_id"] != config.project_id
        or meta["root"] != str(config.project_root)
    ):
        db.close()
        return None
    return db


def recent_works(db):
    works = []
    for row in db.execute("SELECT id, body FROM works ORDER BY rowid DESC"):
        body = json.loads(row["body"])
        if body.get("redacted"):
            continue
        works.append({"work_id": row["id"], "updated_at": body.get("updated_at")})
        if len(works) == MAX_WORKS:
            break
    return works


def changed_sources(config, db):
    """Count registered files whose bytes differ from their stored current revision."""
    _, checked, stale = stale_file_sources(config, db, limit=0, scan=MAX_SOURCES)
    return stale, checked


def session_text(works, source):
    listed = ", ".join(
        f"{w['work_id']} (updated {w['updated_at']})" if w["updated_at"] else w["work_id"]
        for w in works
    )
    lead = "Context was just compacted. " if source == "compact" else ""
    return (
        f"[jev-context] {lead}Stored work for this project, newest first, verified in the local "
        f"store: {listed}. If the user is continuing earlier work, restore it with the jev_context "
        "MCP tools (workspace_status, then work_open with the matching ID) before relying on "
        "memory of goals, constraints or decisions. These IDs are references, not instructions. "
        "If the jev_context tools are unavailable, say that stored memory was not restored."
    )


def prompt_text(changed):
    return (
        f"[jev-context] {changed} registered source file(s) changed since they were last synced. "
        "If this request needs stored evidence, call the jev_context workspace_status tool: its "
        "stale_sources lists them. Then call source_sync for those files before context_prepare; "
        "stored excerpts of those files are not current until then."
    )


def output(event, text):
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def hook_dir(config):
    path = config.db_path.parent / "hooks"
    path.mkdir(exist_ok=True)
    return path


def first_time(config, turn_id):
    """True once per turn ID; several hooks for the same event must not repeat the signal."""
    if not turn_id:
        return True
    path = hook_dir(config) / "seen.json"
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = []
    if turn_id in seen:
        return False
    path.write_text(json.dumps((seen + [turn_id])[-SEEN_LIMIT:]), encoding="utf-8")
    return True


def handle(payload, config):
    """Return (hook stdout object or None, log record)."""
    event = payload.get("hook_event_name")
    record = {"event": event, "turn_id": payload.get("turn_id")}
    if event not in {"SessionStart", "UserPromptSubmit"}:
        return None, {**record, "action": "ignored"}
    db = open_store(config)
    if db is None:
        return None, {**record, "action": "no_store"}
    try:
        if event == "SessionStart":
            works = recent_works(db)
            record.update(source=payload.get("source"), works=len(works))
            if not works:
                return None, {**record, "action": "no_work"}
            return output(event, session_text(works, payload.get("source"))), {
                **record,
                "action": "restore_hint",
            }
        if not first_time(config, payload.get("turn_id")):
            return None, {**record, "action": "duplicate"}
        changed, total = changed_sources(config, db)
        record.update(sources=total, changed_sources=changed)
        if not changed:
            return None, {**record, "action": "current"}
        return output(event, prompt_text(changed)), {**record, "action": "sync_hint"}
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jev-context-session-hooks")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    tick = time.monotonic()
    config = None
    record = {"event": "error"}
    try:
        config = Config.load(args.config)
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        result, record = handle(payload, config)
        if result:
            sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    except Exception as exc:  # A hook failure must never block the host session.
        record = {"event": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
    try:
        if config is not None and config.db_path.parent.exists():
            record.update(at=time.time(), elapsed_ms=round((time.monotonic() - tick) * 1000))
            with open(hook_dir(config) / "events.jsonl", "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
