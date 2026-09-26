"""User-controlled GPU lifecycle; no allocation from normal MCP requests."""

import json
import os
import subprocess
from pathlib import Path

from .common import DomainError
from .modal_engine import descriptor, exchange
from .service import Service
from .storage import FileLock


def modal_profile(config):
    """The configured OpenJev Modal profile, or a DomainError naming what is missing."""
    if "profile_file" not in config.engine:
        raise DomainError(
            "invalid_argument", "Configure engine.profile_file with an OpenJev Modal profile first"
        )
    profile_path = Path(config.engine["profile_file"])
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainError(
            "invalid_argument", f"Cannot read the engine profile: {profile_path}"
        ) from exc
    if not isinstance(profile, dict) or profile.get("family") != "openjev_modal":
        raise DomainError("invalid_argument", "Configure an OpenJev Modal profile first")
    if not isinstance(profile.get("session_file"), str):
        raise DomainError("invalid_argument", "The OpenJev Modal profile has no session_file")
    return profile_path, profile


def control(config, args):
    profile_path, profile = modal_profile(config)
    path = Path(profile["session_file"])
    try:
        session = descriptor(path)
        current = exchange(session, "/status", {}, 1)
    except DomainError:
        session, current = None, {"state": "unavailable"}
        # The owner may still be loading weights, before the HTTP bridge exists.
        try:
            with FileLock(path.with_suffix(".lock"), timeout=0.01):
                pass
        except DomainError:
            try:
                preparing = json.loads(path.with_suffix(".status.json").read_text(encoding="utf-8"))
                if preparing["project_id"] == config.project_id:
                    current = preparing
            except (OSError, ValueError, KeyError):
                pass
    if args.command == "session-status":
        return current
    if args.command == "session-stop":
        if session:
            return exchange(session, "/stop", {}, 1)
        if current["state"] in {"starting", "preparing", "ready"}:
            path.with_suffix(".stop").write_text("stop", encoding="utf-8")
            return {"state": "stopping"}
        return {"state": "unavailable"}
    if not 360 <= args.ttl <= 1100 or not 15 <= args.idle <= 180:
        raise DomainError("invalid_argument", "ttl 360..1100 seconds; idle 15..180 seconds")
    service = Service(config)
    try:
        work = service.work(args.work_id)
        if work.get("redacted"):
            raise DomainError("input_incomplete", "Work origin is unavailable")
    finally:
        service.close()
    if current["state"] != "unavailable":
        if current["project_id"] == config.project_id and current["work_id"] == args.work_id:
            return current
        raise DomainError("engine_busy", "Stop the existing work session first")
    root = Path(__file__).resolve().parents[2]
    log = path.with_suffix(".log")
    path.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(
            [
                profile["modal_python"],
                "-u",
                str(root / "scripts/modal_session.py"),
                "--profile",
                str(profile_path),
                "--project-id",
                config.project_id,
                "--work-id",
                args.work_id,
                "--ttl",
                str(args.ttl),
                "--idle",
                str(args.idle),
            ],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            env={
                **os.environ,
                "MODAL_CONFIG_PATH": profile["modal_config_file"],
                "PYTHONUTF8": "1",
            },
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    return {"state": "starting", "pid": process.pid, "work_id": args.work_id, "log": str(log)}
