"""Optional, model-free Workroom derived memory. No dependency on Workroom code or DB."""

from __future__ import annotations

import os
import sqlite3
import sys
import unicodedata
import uuid
from contextlib import closing
from pathlib import Path

from jsonschema import Draft202012Validator

from .common import DomainError, digest, dumps, now, strict_loads
from .policy import Config
from .storage import FileLock, protect_directory

CONTRACT = "workroom-jev/1"
UUID_SCHEMA = {"type": "string", "pattern": "^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"}
FIELDS = {"title": 200, "summary": 8000, "contribution": 3000, "limitations": 4000}


def schema(properties):
    properties = {"contract": {"const": CONTRACT}, **properties}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


SCHEMAS = {
    "bridge_status": schema({}),
    "bridge_publish": schema(
        {
            **{name: UUID_SCHEMA for name in ("originId", "productId", "reportId")},
            "revision": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
            **{name: {"type": "string", "maxLength": size} for name, size in FIELDS.items()},
        }
    ),
    "bridge_search": schema(
        {
            "originId": UUID_SCHEMA,
            "productId": UUID_SCHEMA,
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        }
    ),
}
DESCRIPTIONS = {
    "bridge_status": "Workroom bridge contract and workspace identity; no model or collection",
    "bridge_publish": "Explicitly publish reported derived memory scoped to origin/product/report",
    "bridge_search": "Search only reported Workroom memory in the requested origin/product scope",
}
SQL = """
CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK(id=1), contract TEXT NOT NULL,
 project_id TEXT NOT NULL, root TEXT NOT NULL);
CREATE TABLE memories (
 origin_id TEXT NOT NULL, product_id TEXT NOT NULL, report_id TEXT NOT NULL,
 memory_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL, content_hash TEXT NOT NULL,
 body TEXT NOT NULL, search_text TEXT NOT NULL,
 provenance TEXT NOT NULL CHECK(provenance='reported'), updated_at TEXT NOT NULL,
 PRIMARY KEY(origin_id, product_id, report_id));
CREATE INDEX scope_updated ON memories(origin_id, product_id, updated_at);
"""


def normalize(value):
    return unicodedata.normalize("NFKC", value).casefold()


def database_path(config):
    return config.db_path.parent / "workroom" / "workroom-bridge.sqlite"


def checked_path(config):
    path = database_path(config)
    if not path.resolve().is_relative_to(config.data_root):
        raise DomainError("storage_failed", "Bridge storage escaped its data directory")
    for current in (path, *path.parents):
        if current == config.data_root:
            break
        if current.is_symlink() or current.is_junction():
            raise DomainError("storage_failed", "Bridge storage cannot use symlinks or junctions")
    return path


def check_meta(db, config):
    row = db.execute("SELECT contract,project_id,root FROM meta WHERE id=1").fetchone()
    if row != (CONTRACT, config.project_id, str(config.project_root)):
        raise DomainError("workspace_mismatch", "Bridge storage belongs to another workspace")


def validate(name, arguments):
    if name not in SCHEMAS:
        raise DomainError("unknown_tool", "Unknown bridge tool")
    if not isinstance(arguments, dict) or arguments.get("contract") != CONTRACT:
        raise DomainError("contract_mismatch", "Expected workroom-jev/1")
    errors = list(Draft202012Validator(SCHEMAS[name]).iter_errors(arguments))
    if errors:
        raise DomainError("invalid_argument", "Arguments do not match the bridge schema")
    try:
        dumps(arguments).encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise DomainError("invalid_argument", "Arguments must be valid UTF-8 JSON") from exc
    if name == "bridge_publish" and not arguments["title"].strip():
        raise DomainError("invalid_argument", "Title must not be blank")
    if name == "bridge_search" and not arguments["query"].strip():
        raise DomainError("invalid_argument", "Query must not be blank")
    result = dict(arguments)
    for key in ("originId", "productId", "reportId"):
        if key in result:
            result[key] = str(uuid.UUID(result[key]))
    return result


def publish(config, args):
    if config.read_only:
        raise DomainError("read_only", "Cannot publish to a read-only workspace")
    path = checked_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        protect_directory(path.parent)
    else:
        path.parent.chmod(0o700)
    key = tuple(args[name] for name in ("originId", "productId", "reportId"))
    body = {name: args[name] for name in FIELDS}
    encoded = dumps(body)
    content_hash = digest(encoded.encode())
    with FileLock(path.parent / "write.lock", timeout=2):
        with closing(sqlite3.connect(path, timeout=2, isolation_level=None)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    # Do not use executescript: it would commit the active transaction.
                    for statement in SQL.split(";"):
                        if statement.strip():
                            db.execute(statement)
                    db.execute(
                        "INSERT INTO meta VALUES(1,?,?,?)",
                        (CONTRACT, config.project_id, str(config.project_root)),
                    )
                check_meta(db, config)
                row = db.execute(
                    "SELECT memory_id,revision,content_hash FROM memories WHERE origin_id=? AND product_id=? AND report_id=?",
                    key,
                ).fetchone()
                status = "created"
                memory_id = "memory-" + uuid.uuid4().hex
                if row:
                    memory_id, revision, previous_hash = row
                    if args["revision"] < revision:
                        raise DomainError(
                            "revision_conflict", "An older revision cannot replace newer memory"
                        )
                    if args["revision"] == revision:
                        if content_hash != previous_hash:
                            raise DomainError(
                                "revision_conflict", "This revision already has different content"
                            )
                        status = "unchanged"
                    else:
                        status = "updated"
                if status != "unchanged":
                    used = (
                        db.execute("PRAGMA page_count").fetchone()[0]
                        * db.execute("PRAGMA page_size").fetchone()[0]
                    )
                    if used + len(encoded.encode()) * 3 > config.storage_limit_bytes:
                        raise DomainError("storage_full", "Bridge storage limit reached")
                    db.execute(
                        """INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(origin_id,product_id,report_id) DO UPDATE SET
                        revision=excluded.revision,content_hash=excluded.content_hash,
                        body=excluded.body,search_text=excluded.search_text,updated_at=excluded.updated_at""",
                        (
                            *key,
                            memory_id,
                            args["revision"],
                            content_hash,
                            encoded,
                            normalize("\n".join(body.values())),
                            "reported",
                            now(),
                        ),
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
    return {
        "contract": CONTRACT,
        "status": status,
        "memoryId": memory_id,
        "revision": args["revision"],
    }


def search(config, args):
    path = checked_path(config)
    if not path.exists():
        return {"contract": CONTRACT, "items": [], "truncated": False}
    terms = list(dict.fromkeys(normalize(args["query"]).split()))
    where = " AND ".join("instr(search_text,?)>0" for _ in terms)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        check_meta(db, config)
        rows = db.execute(
            "SELECT memory_id,report_id,revision,body FROM memories WHERE origin_id=? AND product_id=? AND "
            + where
            + " ORDER BY updated_at DESC,memory_id LIMIT ?",
            (args["originId"], args["productId"], *terms, args["limit"] + 1),
        ).fetchall()
    return {
        "contract": CONTRACT,
        "items": [
            {"memoryId": row[0], "reportId": row[1], "revision": row[2], **strict_loads(row[3])}
            for row in rows[: args["limit"]]
        ],
        "truncated": len(rows) > args["limit"],
    }


def call(config, name, arguments):
    """Return plain bridge JSON and an error flag, never a Jev service envelope."""
    try:
        args = validate(name, arguments)
        if name == "bridge_status":
            result = {
                "contract": CONTRACT,
                "workspaceId": config.project_id,
                "workspaceRoot": str(config.project_root),
                "capabilities": ["publish", "search"],
            }
        elif name == "bridge_publish":
            result = publish(config, args)
        else:
            result = search(config, args)
        return result, False
    except DomainError as exc:
        return {"contract": CONTRACT, "error": {"code": exc.code, "message": exc.message}}, True
    except (sqlite3.Error, OSError, ValueError):
        return {
            "contract": CONTRACT,
            "error": {
                "code": "storage_failed",
                "message": "Bridge storage is unavailable; no successful write was reported",
            },
        }, True


def launch_description(config_path):
    config_path = Path(config_path).resolve(strict=True)
    config = Config.load(config_path)
    return {
        "contract": CONTRACT,
        "command": str(Path(sys.executable).absolute()),
        "args": [
            "-I",
            "-X",
            "utf8",
            str(Path(__file__).with_name("bridge_entry.py").resolve()),
            "--config",
            str(config_path),
        ],
        "cwd": str(config.project_root),
    }
