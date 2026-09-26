from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from .common import DomainError, dumps

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_meta (
 id INTEGER PRIMARY KEY CHECK(id=1), project_id TEXT NOT NULL, root TEXT NOT NULL,
 schema_version INTEGER NOT NULL, data_revision INTEGER NOT NULL DEFAULT 0,
 source_set_revision INTEGER NOT NULL DEFAULT 0, policy_hash TEXT NOT NULL,
 policy_revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS works (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, body TEXT NOT NULL, UNIQUE(work_id,revision));
CREATE TABLE IF NOT EXISTS decisions (
 id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issues (
 id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, locator TEXT NOT NULL, current_revision TEXT,
 status TEXT NOT NULL, origin TEXT NOT NULL, observed_at TEXT NOT NULL, UNIQUE(kind,locator));
CREATE TABLE IF NOT EXISTS source_revisions (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 sha256 TEXT NOT NULL, raw BLOB NOT NULL, text TEXT NOT NULL, origin TEXT NOT NULL,
 observed_at TEXT NOT NULL, UNIQUE(source_id,sha256), UNIQUE(source_id,id));
CREATE TABLE IF NOT EXISTS chunks (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, revision TEXT NOT NULL,
 start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, byte_start INTEGER NOT NULL,
 byte_end INTEGER NOT NULL, text TEXT NOT NULL, search_text TEXT NOT NULL, neighbors TEXT NOT NULL,
 FOREIGN KEY(source_id,revision) REFERENCES source_revisions(source_id,id) ON DELETE CASCADE);
CREATE VIRTUAL TABLE IF NOT EXISTS lexical USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS trigrams USING fts5(chunk_id UNINDEXED, text, tokenize='trigram');
CREATE TABLE IF NOT EXISTS source_refs (
 owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, source_id TEXT NOT NULL, revision TEXT NOT NULL,
 locator TEXT NOT NULL,
 FOREIGN KEY(source_id,revision) REFERENCES source_revisions(source_id,id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS refs_source ON source_refs(source_id);
CREATE TABLE IF NOT EXISTS packets (
 id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
 body TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mutations (
 tool TEXT NOT NULL, id TEXT NOT NULL, input_hash TEXT NOT NULL, work_id TEXT,
 state TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL, PRIMARY KEY(tool,id));
CREATE TABLE IF NOT EXISTS sync_items (
 mutation_id TEXT NOT NULL, item_index INTEGER NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(mutation_id,item_index));
CREATE TABLE IF NOT EXISTS tombstones (
 kind TEXT NOT NULL, id TEXT NOT NULL, deleted_at TEXT NOT NULL, PRIMARY KEY(kind,id));
"""


class Store:
    def __init__(self, config, *, migrate=False):
        self.config = config
        self.path = config.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.path.parent.chmod(0o700)
        else:
            protect_directory(self.path.parent)
        self.db = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        with FileLock(self.path.parent / "schema.lock", timeout=1):
            exists = self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='project_meta'"
            ).fetchone()
            if exists:
                meta = self.meta()
                if meta["project_id"] != config.project_id or meta["root"] != str(
                    config.project_root
                ):
                    raise DomainError(
                        "policy_denied", "Database belongs to another project or path"
                    )
                expected_schema = int(config.contract_version[0])
                if meta["schema_version"] == 1 and expected_schema == 2 and migrate:
                    if config.read_only:
                        raise DomainError(
                            "policy_denied", "Cannot migrate a read-only installation"
                        )
                    if meta["policy_hash"] != replace(config, contract_version="1.0").policy_hash:
                        raise DomainError(
                            "policy_denied", "Migrate without changing the source policy"
                        )
                    backup_path = self.path.with_suffix(".v1-backup.sqlite")
                    if backup_path.exists():
                        raise DomainError("storage_failed", "Migration backup already exists")
                    backup = sqlite3.connect(backup_path)
                    try:
                        self.db.backup(backup)
                    finally:
                        backup.close()
                    with self.transaction():
                        self.db.execute(
                            "UPDATE project_meta SET schema_version=2,policy_hash=?,data_revision=data_revision+1 WHERE id=1",
                            (config.policy_hash,),
                        )
                        self.db.execute("UPDATE packets SET status='stale'")
                    meta = self.meta()
                if meta["schema_version"] != expected_schema:
                    raise DomainError(
                        "storage_failed", "Unsupported database schema; no automatic reset"
                    )
            self.db.executescript(SCHEMA)
            with self.transaction():
                self.db.execute(
                    "INSERT OR IGNORE INTO project_meta(id,project_id,root,schema_version,policy_hash) VALUES(1,?,?,?,?)",
                    (
                        config.project_id,
                        str(config.project_root),
                        int(config.contract_version[0]),
                        config.policy_hash,
                    ),
                )
                if self.meta()["policy_hash"] != config.policy_hash:
                    if config.policy_revision <= self.meta()["policy_revision"]:
                        raise DomainError(
                            "policy_denied",
                            "Changed allowlist requires a higher installation policy_revision",
                        )
                    self.db.execute(
                        "UPDATE project_meta SET policy_hash=?,policy_revision=? WHERE id=1",
                        (config.policy_hash, config.policy_revision),
                    )
                    self.bump(source=True)
                    self.db.execute("DELETE FROM packets")
                    self.redact_replays("policy_changed")

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def meta(self):
        return dict(self.db.execute("SELECT * FROM project_meta WHERE id=1").fetchone())

    def bump(self, source=False):
        self.db.execute(
            "UPDATE project_meta SET data_revision=data_revision+1, source_set_revision=source_set_revision+? WHERE id=1",
            (int(source),),
        )
        if source:
            self.db.execute("UPDATE packets SET status='stale'")

    def check_space(self, additional=0):
        used = (
            self.db.execute("PRAGMA page_count").fetchone()[0]
            * self.db.execute("PRAGMA page_size").fetchone()[0]
        )
        free = (
            self.db.execute("PRAGMA freelist_count").fetchone()[0]
            * self.db.execute("PRAGMA page_size").fetchone()[0]
        )
        if used - free + additional > self.config.storage_limit_bytes:
            raise DomainError("storage_limit", "Project storage budget exceeded")

    def body(self, table, object_id, work_id=None):
        assert table in {"works", "events", "decisions", "issues", "evidence", "packets"}
        row = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()
        if row is None or (work_id and row["work_id"] != work_id):
            raise DomainError("not_found", "Object not found in this work")
        return json.loads(row["body"])

    def put_body(self, table, object_id, body):
        assert table in {"works", "events", "decisions", "issues", "evidence"}
        self.db.execute(f"UPDATE {table} SET body=? WHERE id=?", (dumps(body), object_id))

    def refs(self, owner_type, owner_id, refs):
        for ref in refs:
            row = self.db.execute(
                "SELECT text FROM source_revisions WHERE source_id=? AND id=?",
                (ref["source_id"], ref["revision"]),
            ).fetchone()
            if not row:
                raise DomainError("not_found", "Referenced source revision not found")
            if "start_line" in ref and not (
                ref["start_line"] <= ref["end_line"] <= max(1, len(row["text"].splitlines()))
            ):
                raise DomainError("invalid_argument", "Invalid reference line range")
            self.db.execute(
                "INSERT INTO source_refs VALUES(?,?,?,?,?)",
                (owner_type, owner_id, ref["source_id"], ref["revision"], dumps(ref)),
            )

    def inherited_refs(self, owner_type, owner_id):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT locator FROM source_refs WHERE owner_type=? AND owner_id=?",
                (owner_type, owner_id),
            )
        ]

    def redact_replays(self, reason):
        # Conservative: remove every cached body, including derived summaries without literal quotes.
        pending = self.db.execute(
            "SELECT tool,id FROM mutations WHERE result IS NULL AND tool='source_sync'"
        ).fetchall()
        for row in pending:
            safe = {
                "contract_version": "1.0",
                "request_id": "redacted",
                "outcome": "insufficient",
                "data": {"redacted": True},
                "warnings": [
                    {
                        "code": reason,
                        "message": "Interrupted sync invalidated; review scope before a new mutation",
                    }
                ],
                "error": None,
            }
            self.db.execute(
                "UPDATE mutations SET state='complete',result=? WHERE tool=? AND id=?",
                (dumps(safe), row["tool"], row["id"]),
            )
        for row in self.db.execute(
            "SELECT tool,id,result FROM mutations WHERE result IS NOT NULL"
        ).fetchall():
            result = json.loads(row["result"])
            result["data"] = {
                k: v
                for k, v in result["data"].items()
                if k in {"work_id", "revision", "event_id", "source_id", "data_revision"}
            }
            result["data"]["redacted"] = True
            result["warnings"] = [
                {"code": reason, "message": "Replay body invalidated; inspect current state"}
            ]
            self.db.execute(
                "UPDATE mutations SET result=? WHERE tool=? AND id=?",
                (dumps(result), row["tool"], row["id"]),
            )


class FileLock:
    """OS-owned lock; a dead process cannot leave a live lease."""

    def __init__(self, path: Path, timeout=0):
        self.path = path
        self.file = None
        self.timeout = timeout

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a+b")
        if self.path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() < deadline:
                    time.sleep(0.01)
                    continue
                self.file.close()
                self.file = None
                raise DomainError("busy", "Another process owns this operation", True) from exc
        return self

    def __exit__(self, *args):
        if self.file:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()


def protect_directory(path):
    """Restrict this dedicated database directory to the current OS user and SYSTEM."""
    import ntsecuritycon
    import win32api
    import win32security

    try:
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        try:
            user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        acl = win32security.ACL()
        inheritance = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        for sid in (user, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)):
            acl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION_DS, inheritance, ntsecuritycon.FILE_ALL_ACCESS, sid
            )
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            acl,
            None,
        )
    except Exception as exc:
        raise DomainError("storage_failed", "Cannot protect local database directory") from exc
