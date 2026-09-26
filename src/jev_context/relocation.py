"""Explicitly rebind an existing store after moving its project directory.

Stop the project's servers before invoking this module. This operation preserves
history and never changes directory permissions or creates a missing database.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import sqlite3
import sys
from pathlib import Path

from .common import DomainError, dumps
from .policy import Config
from .storage import FileLock


def _invalidate_cached_results(db, contract_version):
    db.execute("DELETE FROM packets")
    for row in db.execute("SELECT tool,id,result FROM mutations").fetchall():
        if row["result"] is None:
            if row["tool"] != "source_sync":
                continue
            result = {
                "contract_version": contract_version,
                "request_id": "redacted",
                "outcome": "insufficient",
                "data": {},
                "error": None,
            }
        else:
            result = json.loads(row["result"])
        result["data"] = {
            key: value
            for key, value in result["data"].items()
            if key in {"work_id", "revision", "event_id", "source_id", "data_revision"}
        }
        result["data"]["redacted"] = True
        result["warnings"] = [
            {"code": "project_relocated", "message": "Replay invalidated; inspect current state"}
        ]
        db.execute(
            "UPDATE mutations SET state='complete',result=? WHERE tool=? AND id=?",
            (dumps(result), row["tool"], row["id"]),
        )


def relocate_project(config: Config, *, expected_old_root, backup_path):
    """Back up and relocate only when identity and the previous policy match.

    ``config`` describes the new root and the existing database location. The
    expected old root is an exact, absolute identity assertion; it need not exist.
    A write reservation keeps the checked state unchanged throughout the backup.
    """
    old_root = Path(expected_old_root)
    if not old_root.is_absolute() or str(old_root) == str(config.project_root):
        raise DomainError("invalid_argument", "Specify a different absolute previous project root")
    if config.read_only:
        raise DomainError("policy_denied", "Cannot relocate a read-only installation")
    backup_path = Path(backup_path).resolve()
    db_path = config.db_path.resolve()
    if backup_path in {db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")}:
        raise DomainError("invalid_argument", "Backup must be separate from the live database")
    # Sync keeps its ingestion lock while reading files outside SQLite transactions.
    # Hold both Store locks through backup and commit, not only the write reservation.
    with (
        FileLock(db_path.parent / "ingestion.lock"),
        FileLock(db_path.parent / "schema.lock"),
    ):
        return _relocate_locked(config, old_root, backup_path, db_path)


def _relocate_locked(config, old_root, backup_path, db_path):
    # Copying avoids Config.__post_init__: the previous directory may no longer exist.
    previous_config = copy.copy(config)
    previous_config.project_root = old_root
    db = sqlite3.connect(db_path.as_uri() + "?mode=rw", uri=True, timeout=1, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT * FROM project_meta WHERE id=1").fetchone()
            if row is None:
                raise DomainError("storage_failed", "Project metadata is missing")
            meta = dict(row)
            if meta["project_id"] != config.project_id or meta["root"] != str(old_root):
                raise DomainError(
                    "policy_denied", "Database project or previous root does not match"
                )
            if meta["schema_version"] != int(config.contract_version[0]):
                raise DomainError("storage_failed", "Relocation cannot change the database schema")
            if meta["policy_hash"] != previous_config.policy_hash:
                raise DomainError("policy_denied", "Relocation cannot change the source policy")
            if [r[0] for r in db.execute("PRAGMA integrity_check")] != ["ok"]:
                raise DomainError("storage_failed", "Database integrity check failed")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise DomainError("storage_failed", "Database foreign key check failed")

            # O_EXCL prevents accidentally replacing an earlier recovery copy.
            descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            with contextlib.closing(
                sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=1)
            ) as source:
                with contextlib.closing(sqlite3.connect(backup_path)) as backup:
                    source.backup(backup)
                    if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise DomainError("storage_failed", "Backup integrity check failed")
            revision = max(config.policy_revision, meta["policy_revision"] + 1)
            db.execute(
                "UPDATE project_meta SET root=?,policy_hash=?,policy_revision=?,"
                "data_revision=data_revision+1,source_set_revision=source_set_revision+1 WHERE id=1",
                (str(config.project_root), config.policy_hash, revision),
            )
            _invalidate_cached_results(db, config.contract_version)
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    finally:
        db.close()
    return {
        "project_id": config.project_id,
        "previous_root": str(old_root),
        "project_root": str(config.project_root),
        "backup_path": str(backup_path),
        "policy_revision": revision,
        "data_revision": meta["data_revision"] + 1,
        "source_set_revision": meta["source_set_revision"] + 1,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--expected-old-root", required=True)
    parser.add_argument("--backup-path", required=True)
    args = parser.parse_args(argv)
    try:
        result = relocate_project(
            Config.load(args.config),
            expected_old_root=args.expected_old_root,
            backup_path=args.backup_path,
        )
    except (DomainError, OSError, sqlite3.Error, ValueError) as exc:
        print(dumps({"outcome": "error", "message": str(exc)}), file=sys.stderr)
        return 1
    print(dumps({"outcome": "ok", "data": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
