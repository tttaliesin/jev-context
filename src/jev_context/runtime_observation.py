"""Source fingerprints and bounded, content-free MCP response observations.

A prepared response is not proof of transport delivery, host receipt or model use.
No CLI or desktop read calls observe_response.
"""

from __future__ import annotations

import json
from pathlib import Path

from .budget import measure
from .common import DomainError, digest, dumps, now, uid
from .project_files import atomic_write, safe_path
from .storage import FileLock


def source_fingerprint():
    try:
        root = Path(__file__).parent
        paths = sorted([*root.glob("*.py"), root / "contracts.json"])
        return digest(
            dumps([(path.name, digest(path.read_bytes())) for path in paths]).encode("utf-8")
        )
    except OSError:
        return None


STARTUP_BUILD = source_fingerprint()


class RuntimeIdentity:
    def __init__(self, transport):
        self.identity = {
            "instance_id": uid("runtime"),
            "started_at": now(),
            "service_version": "0.2.0",
            "build_hash": STARTUP_BUILD,
            "transport": transport,
        }

    def snapshot(self, contract_version):
        disk = source_fingerprint()
        return {
            **self.identity,
            "contract_version": contract_version,
            "disk_build_hash": disk,
            "code_state": compare_build(self.identity["build_hash"], disk),
        }


def compare_build(loaded, disk):
    if not loaded or not disk:
        return "unknown"
    return "matches_disk" if loaded == disk else "restart_required"


def _path(config):
    return safe_path(config.project_root, ".local/jev-runtime/observation.json")


def _read(config):
    path = _path(config)
    if not path.exists():
        return None
    if path.stat().st_size > 32768:
        raise ValueError("Oversized runtime observation")
    item = json.loads(path.read_text("utf-8"))
    if not isinstance(item, dict) or item.get("schema") != 1:
        raise ValueError("Invalid runtime observation")
    return item


def observe_response(config, name, arguments, result, runtime, client_name, config_hash):
    """Best-effort metadata only. Health probes and read-only installs never write."""
    if (
        config.read_only
        or name not in {"workspace_status", "context_prepare"}
        or result.get("outcome") not in {"ok", "partial", "insufficient"}
        or client_name == "jev-context-probe"
        or not config_hash
    ):
        return

    try:
        with FileLock(safe_path(config.project_root, ".local/jev-runtime/observation.lock")):
            try:
                item = _read(config)
            except (OSError, ValueError, DomainError):
                item = None
            if not item or (
                item.get("project_id") != config.project_id
                or item.get("config_hash") != config_hash
            ):
                item = {"schema": 1, "project_id": config.project_id, "config_hash": config_hash}
            observation = {
                "observed_at": now(),
                "stage": "response_prepared",
                "host_receipt": "not_observed",
                "answer_use": "not_observed",
                "client_kind": "codex" if "codex" in str(client_name).lower() else "other_mcp",
                "client_identity": "self_reported",
                "runtime": runtime,
                "outcome": result["outcome"],
            }
            if name == "context_prepare":
                data = result.get("data", {})
                observation.update(
                    work_id=arguments.get("work_id"),
                    packet_id=data.get("packet_id"),
                    work_revision=data.get("work_revision"),
                    source_set_revision=data.get("source_set_revision"),
                    evidence_count=len(data.get("evidence", [])),
                    wire_bytes=measure(result),
                    token_count=None,
                    retrieval_status=data.get("coverage", {}).get("retrieval_status"),
                )
                item["last_context"] = observation
            else:
                item["last_status"] = observation
            atomic_write(_path(config), dumps(item).encode())
    except (OSError, ValueError, DomainError):
        pass  # Diagnostics must never replace the tool's original response.


def observation_status(config, config_path):
    try:
        item = _read(config)
        if not item:
            return {"state": "not_observed"}
        if item.get("project_id") != config.project_id:
            return {"state": "project_mismatch"}
        if item.get("config_hash") != digest(Path(config_path).read_bytes()):
            return {"state": "settings_changed"}
        disk = source_fingerprint()
        # Reject corrupted sidecars instead of exposing arbitrary local text to the UI.
        for key in ("last_status", "last_context"):
            value = item.get(key)
            if value is None:
                continue
            if (
                not isinstance(value, dict)
                or value.get("stage") != "response_prepared"
                or not isinstance(value.get("runtime"), dict)
                or value.get("outcome") not in {"ok", "partial", "insufficient"}
            ):
                raise ValueError("Invalid observation")
            value["runtime"]["code_state"] = compare_build(value["runtime"].get("build_hash"), disk)
        return {
            "state": "recorded",
            "last_status": item.get("last_status"),
            "last_context": item.get("last_context"),
            "current_session": "not_observed",
        }
    except (OSError, ValueError, DomainError, TypeError):
        return {"state": "unavailable"}
