"""Narrow JSON-lines API for the desktop manager; no renderer-supplied commands or tools.

Requests are {id, method, params}; responses are {id, result} or {id, error}.
Opening/closing the manager only creates/closes a lazy model proxy. Model preparation and
authenticated shared-host stopping are explicit methods, never side effects of a status read.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

from .cli import prepare_engine
from .common import DomainError, dumps, now, strict_loads, uid
from .policy import Config
from .service import CONTRACT, CONTRACT_V2, Service
from .workroom_bridge import SCHEMAS as BRIDGE_SCHEMAS

MAX_REQUEST = 65536
METHODS = {
    "overview": {"config_path", "cursor", "limit"},
    "work_open": {"work_id"},
    "model_prepare": set(),
    "model_stop": set(),
    "connection_check": set(),
}


def error_data(exc):
    return {"code": exc.code, "message": exc.message, "retryable": exc.retryable}


class DesktopBridge:
    def __init__(self, config_path):
        self.engine = None
        self.select(config_path)

    def select(self, config_path):
        path = Path(config_path).resolve(strict=True)
        config = Config.load(path)
        engine, profile, profile_error = None, {}, None
        if config.engine.get("state", "disabled") in {"shadow", "active"}:
            try:
                profile = strict_loads(Path(config.engine["profile_file"]).read_text("utf-8"))
                if not isinstance(profile, dict):
                    raise ValueError("The local model profile must be an object")
                if profile.get("family") not in {"laya", "semif_openvino"}:
                    raise DomainError(
                        "unsupported_engine", "The manager supports local model profiles"
                    )
                engine = prepare_engine(config)  # Local adapters return a lazy proxy only.
            except DomainError as exc:
                profile_error = error_data(exc)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                profile_error = error_data(DomainError("invalid_profile", str(exc)[:300]))
        previous = self.engine
        self.config_path, self.config = path, config
        self.engine, self.profile, self.profile_error = engine, profile, profile_error
        self.connection = {
            "manager_bridge": "connected",
            "mcp_stdio": "not_checked",
            "desktop_current_session": "not_observed",
        }
        if previous:
            previous.close()  # A proxy does not own the shared worker.

    def close(self):
        if self.engine:
            self.engine.close()

    def arguments(self, **values):
        result = {"request_id": uid("desktop"), **values}
        if self.config.contract_version == "2.0":
            result["contract_version"] = "2.0"
        return result

    def service_call(self, tool, **values):
        if not self.config.db_path.is_file():
            raise DomainError(
                "not_initialized", "The selected project has no existing work database"
            )
        service = Service(self.config, engine=self.engine)
        try:
            result = service.call(tool, self.arguments(**values))
        finally:
            service.close()
        if result["outcome"] != "ok":
            item = result.get("error") or {}
            raise DomainError(
                item.get("code", "service_failed"),
                item.get("message", "Read failed"),
                item.get("retryable", False),
            )
        return result["data"]

    def model_snapshot(self):
        snapshot = (
            self.engine.snapshot()
            if self.engine
            else {
                "state": "unavailable" if self.profile_error else "disabled",
                "broker_pid": None,
                "worker_pid": None,
                "instance_id": None,
            }
        )
        return {
            **snapshot,
            "configured_mode": self.config.engine.get("state", "disabled"),
            "family": self.profile.get("family"),
            "profile_file": self.config.engine.get("profile_file"),
            "profile_fingerprint": self.engine.fingerprint if self.engine else None,
            "model_revision": self.profile.get("model_revision"),
            "device": self.profile.get("device"),
            "idle_timeout_seconds": snapshot.get(
                "idle_timeout_seconds", self.profile.get("idle_timeout_seconds", 120)
            ),
            "profile_error": self.profile_error,
            "can_prepare": self.engine is not None
            and snapshot["state"] not in {"preparing", "shadow"},
            "can_stop": bool(snapshot.get("broker_pid"))
            and not snapshot.get("active_requests")
            and snapshot["state"] != "preparing",
        }

    def overview(self, params=None):
        params = params or {}
        if "config_path" in params:
            if not isinstance(params["config_path"], str) or not params["config_path"]:
                raise DomainError(
                    "invalid_argument", "config_path must name a project configuration"
                )
            self.select(params["config_path"])
        if "limit" in params and (
            type(params["limit"]) is not int or not 1 <= params["limit"] <= 50
        ):
            raise DomainError("invalid_argument", "limit must be an integer from 1 to 50")
        workspace_error = None
        try:
            data = self.service_call(
                "workspace_status",
                **{key: params[key] for key in ("cursor", "limit") if key in params},
            )
        except DomainError as exc:
            # A missing/broken installation should still show its model and configuration state.
            if exc.code in {"invalid_argument", "cursor_stale"}:
                raise
            workspace_error, data = error_data(exc), {}
        return {
            "config_path": str(self.config_path),
            "project": {
                "project_id": self.config.project_id,
                "project_root": str(self.config.project_root),
                "data_root": str(self.config.data_root),
                "database_path": str(self.config.db_path),
                "contract_version": self.config.contract_version,
                "read_only": self.config.read_only,
            },
            "engine": self.model_snapshot(),  # Query live state after the database read.
            "works": {
                key: data.get(key, [] if key == "items" else None)
                for key in ("items", "next_cursor", "list_revision")
            },
            "sources": {"count": data.get("sources"), "stale_sources": data.get("stale_sources")},
            "workspace_error": workspace_error,
            "connection": dict(self.connection),
        }

    def require_engine(self):
        if not self.engine:
            raise DomainError("engine_unavailable", "No usable local model profile is configured")
        return self.engine

    def model_prepare(self):
        self.require_engine().prepare()
        return self.overview()

    def model_stop(self):
        engine = self.require_engine()
        if engine.snapshot().get("broker_pid"):
            # The authenticated host rejects active inference or preparation. Never kill a PID.
            engine._call("stop")
        return self.overview()

    async def check_stdio(self):
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        arguments = ["-m", "jev_context", "serve", "--config", str(self.config_path)]
        # Test the MCP transport/database contract only. Model state comes from the separate
        # live proxy; omitting preparation also prevents a changed/remote profile from loading.
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        parameters = StdioServerParameters(command=sys.executable, args=arguments, env=environment)
        with anyio.fail_after(15):
            async with stdio_client(parameters) as streams, ClientSession(*streams) as session:
                await session.initialize()
                listing = await session.list_tools()
                contract = CONTRACT_V2 if self.config.contract_version == "2.0" else CONTRACT
                if {tool.name for tool in listing.tools} != set(contract["tools"]) | set(
                    BRIDGE_SCHEMAS
                ):
                    raise DomainError(
                        "mcp_contract_mismatch", "MCP tool listing differs from the contract"
                    )
                result = await session.call_tool("workspace_status", self.arguments())
                data = result.structuredContent
                texts = [item.text for item in result.content if item.type == "text"]
                if (
                    result.isError
                    or not isinstance(data, dict)
                    or data.get("outcome") != "ok"
                    or len(texts) != 1
                    or strict_loads(texts[0]) != data
                    or data.get("data", {}).get("project_id") != self.config.project_id
                ):
                    raise DomainError("mcp_check_failed", "MCP status response failed verification")
                return {
                    "tools": sorted(tool.name for tool in listing.tools),
                    "project_id": self.config.project_id,
                    "wire_verified": True,
                }

    def connection_check(self):
        import anyio

        self.service_call("workspace_status")  # Reject a missing database before launching MCP.
        self.connection.update(checked_at=now(), mcp_stdio="failed")
        try:
            self.connection.update(anyio.run(self.check_stdio))
            self.connection.update(mcp_stdio="verified", error=None)
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, DomainError)
                else DomainError(
                    "mcp_check_failed",
                    "The independent local MCP connection could not be verified",
                    True,
                )
            )
            self.connection["error"] = error_data(error)
        return self.overview()

    def dispatch(self, request):
        request_id = request.get("id") if isinstance(request, dict) else None
        if type(request_id) not in {str, int}:
            request_id = None
        try:
            if (
                not isinstance(request, dict)
                or set(request) != {"id", "method", "params"}
                or not isinstance(request_id, (str, int))
                or isinstance(request_id, bool)
                or not isinstance(request["method"], str)
                or request["method"] not in METHODS
                or not isinstance(request["params"], dict)
            ):
                raise DomainError("invalid_argument", "Expected a supported JSON-lines request")
            method, params = request["method"], request["params"]
            if set(params) - METHODS[method]:
                raise DomainError("invalid_argument", "Unsupported method parameters")
            if method == "overview":
                result = self.overview(params)
            elif method == "work_open":
                if not isinstance(params.get("work_id"), str):
                    raise DomainError("invalid_argument", "work_id is required")
                result = self.service_call("work_open", work_id=params["work_id"])
            else:
                result = getattr(self, method)()
            return {"id": request_id, "result": result}
        except DomainError as exc:
            return {"id": request_id, "error": error_data(exc)}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {
                "id": request_id,
                "error": error_data(DomainError("invalid_configuration", str(exc)[:300])),
            }
        except Exception:
            return {
                "id": request_id,
                "error": error_data(
                    DomainError("bridge_failed", "The manager operation failed", True)
                ),
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    bridge = DesktopBridge(args.config)
    try:
        while line := sys.stdin.buffer.readline(MAX_REQUEST + 1):
            if len(line) > MAX_REQUEST:
                print(
                    dumps(
                        {
                            "id": None,
                            "error": error_data(
                                DomainError("invalid_argument", "Request too large")
                            ),
                        }
                    ),
                    flush=True,
                )
                break
            try:
                request = strict_loads(line.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                result = {
                    "id": None,
                    "error": error_data(DomainError("invalid_argument", "Invalid JSON request")),
                }
            else:
                with contextlib.redirect_stdout(sys.stderr):
                    result = bridge.dispatch(request)
            print(dumps(result), flush=True)
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
