from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

from .common import DomainError, dumps, strict_loads, uid
from .policy import Config
from .service import CONTRACT, CONTRACT_V2, Service


def write_config(path, root, data, allowed):
    path = Path(path)
    if path.exists():
        raise ValueError("Configuration already exists; edit it explicitly")
    project_id = uid("project")
    # JSON string escaping is compatible with these TOML basic string values.
    content = "\n".join(
        [
            f"project_root = {json.dumps(str(Path(root).resolve()))}",
            f"data_root = {json.dumps(str(Path(data).resolve()))}",
            f"project_id = {json.dumps(project_id)}",
            f"allowed_paths = {json.dumps(allowed)}",
            "read_only = false",
            "",
            "[engine]",
            'state = "disabled"',
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        file.write(content)
    return path


def codex_entry(config_path):
    path = str(Path(config_path).resolve())
    config = Config.load(path)
    arguments = ["-m", "jev_context", "serve", "--config", path]
    if config.engine.get("state") in {"shadow", "active"}:
        arguments.append("--prepare-engine")
    return {
        "command": sys.executable,
        "args": arguments,
        "startup_timeout_sec": 120 if "--prepare-engine" in arguments else 20,
        "tool_timeout_sec": 10,
    }


def prepare_engine(config, background=False):
    """Enable a lazy shared local connection; remote adapters keep their explicit contract.

    ``background`` remains accepted for CLI compatibility. Neither form starts local weights.
    """
    from .engines import Laya, OpenJev, SemifOpenVINO
    from .modal_engine import ModalOpenJev
    from .shared_engine import SharedLocalEngine

    if config.engine.get("state") not in {"shadow", "active"}:
        raise ValueError("Explicit engine preparation requires engine.state=shadow or active")
    profile = json.loads(Path(config.engine["profile_file"]).read_text(encoding="utf-8"))
    factories = {
        "laya": Laya,
        "openjev": OpenJev,
        "openjev_modal": ModalOpenJev,
        "semif_openvino": SemifOpenVINO,
    }
    if profile["family"] not in factories:
        raise ValueError("Unknown engine family")
    if profile["family"] in {"laya", "semif_openvino"}:
        return SharedLocalEngine(profile)
    engine = factories[profile["family"]](profile)
    try:
        engine.prepare()
    except DomainError as exc:
        engine.close()
        config.engine["preparation_error"] = exc.code
        return None
    return engine


def read_call_input(path):
    if path:
        with Path(path).open("rb") as file:
            raw = file.read(524289)
    else:
        raw = sys.stdin.buffer.read(524289)
    if len(raw) > 524288:
        raise ValueError("Input exceeds 524288 bytes")
    value = strict_loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Input must be a JSON object")
    # Exponents can overflow without invoking the JSON parser's parse_constant hook.
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite JSON number")
    return value


def demo():
    with tempfile.TemporaryDirectory(prefix="jev-demo-") as directory:
        root = Path(directory) / "project"
        root.mkdir()
        (root / "rules.md").write_text(
            "# 권한\n설계만 작성. 코드는 별도 요청 후 변경.\nrefreshToken() 검토는 로컬 검사만 허용.\n",
            encoding="utf-8",
        )
        config = Config(root, Path(directory) / "data", uid("project"), ("*.md",))
        service = Service(config)
        created = service.call(
            "work_open",
            dict(
                request_id=uid("req"),
                mutation_id=uid("mut"),
                create=dict(
                    title="한국어 설계 재개",
                    goal="설계 문서 완성",
                    scope=dict(mode="design", constraints=["설계만 작성"]),
                    origin=dict(quote="설계만 작성해줘"),
                ),
            ),
        )
        wid = created["data"]["work_id"]
        synced = service.call(
            "source_sync",
            dict(
                request_id=uid("req"),
                mutation_id=uid("mut"),
                items=[dict(kind="file", relative_path="rules.md")],
            ),
        )
        service.close()
        service = Service(config)
        restored = service.call("work_open", dict(request_id=uid("req"), work_id=wid))
        packet = service.call(
            "context_prepare", dict(request_id=uid("req"), work_id=wid, query="권한 refreshToken")
        )
        service.close()
        if any(r["outcome"] != "ok" for r in (created, synced, restored, packet)):
            raise RuntimeError("Demo failed")
        print(
            dumps(
                dict(
                    restored_goal=restored["data"]["goal"],
                    scope=restored["data"]["scope"],
                    context=packet,
                )
            )
        )


def build_parser():
    parser = argparse.ArgumentParser(prog="jev-context")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser(
        "init", help="Create explicit project configuration; no source collection"
    )
    setup.add_argument("--config", required=True)
    setup.add_argument("--project-root", required=True)
    setup.add_argument("--data-root", required=True)
    setup.add_argument("--allow", action="append", default=[])
    for name in ("serve", "status", "codex-config", "migrate", "call", "schema"):
        command = commands.add_parser(name)
        command.add_argument("--config", required=True)
        if name in {"serve", "call"}:
            command.add_argument(
                "--prepare-engine",
                action="store_true",
                help="Enable demand-started shared local judgments or attach to a Modal session",
            )
        if name in {"call", "schema"}:
            command.add_argument("--tool", required=True, choices=sorted(CONTRACT_V2["tools"]))
        if name == "call":
            command.add_argument("--input", type=Path, help="UTF-8 JSON file; default is stdin")
    commands.add_parser("demo", help="Isolated Korean save/restart/search example")
    for name in ("session-start", "session-status", "session-stop"):
        command = commands.add_parser(name, help="Manage an explicit work-scoped Modal GPU session")
        command.add_argument("--config", required=True)
        if name == "session-start":
            command.add_argument("--work-id", required=True)
            command.add_argument("--ttl", type=int, default=600)
            command.add_argument("--idle", type=int, default=90)
    return parser


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "init":
        print(write_config(args.config, args.project_root, args.data_root, args.allow))
    elif args.command == "demo":
        demo()
    elif args.command == "codex-config":
        print(
            "[mcp_servers.jev_context]\n"
            + "\n".join(
                f"{key} = {json.dumps(value)}" for key, value in codex_entry(args.config).items()
            )
        )
    elif args.command.startswith("session-"):
        from .session_control import control

        try:
            print(dumps(control(Config.load(args.config), args)))
        except DomainError as exc:
            print(dumps({"error": {"code": exc.code, "message": exc.message}}))
            raise SystemExit(1) from exc
    else:
        CONFIGURED_COMMANDS[args.command](parser, Config.load(args.config), args)


def run_schema(parser, config, args):
    contract = CONTRACT_V2 if config.contract_version == "2.0" else CONTRACT
    if args.tool not in contract["tools"]:
        parser.error("Tool is not available in the configured contract")
    print(
        dumps(
            {
                "contract_version": config.contract_version,
                "tool": args.tool,
                "input_schema": contract["tools"][args.tool],
                "output_schema": contract["output"],
            }
        )
    )


def run_call(parser, config, args):
    try:
        arguments = read_call_input(args.input)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        parser.error(str(exc))
    engine = prepare_engine(config) if args.prepare_engine else None
    service = None
    try:
        service = Service(config, engine=engine)
        result = service.call(args.tool, arguments)
        print(dumps(result))
        if result["outcome"] in {"error", "conflict"}:
            raise SystemExit(1)
    finally:
        if service:
            service.close()
        if engine:
            engine.close()


def run_migrate(parser, config, args):
    from .storage import Store

    if config.contract_version != "2.0":
        parser.error("Set contract_version=2.0 explicitly before migration")
    store = Store(config, migrate=True)
    print(
        dumps(
            {
                "schema_version": store.meta()["schema_version"],
                "backup": str(store.path.with_suffix(".v1-backup.sqlite")),
            }
        )
    )
    store.close()


def run_serve(parser, config, args):
    import anyio

    from .server import serve

    engine = prepare_engine(config, background=True) if args.prepare_engine else None
    try:
        anyio.run(serve, config, engine)
    finally:
        if engine:
            engine.close()


def run_status(parser, config, args):
    service = Service(config)
    try:
        arguments = {"request_id": uid("req")}
        if config.contract_version == "2.0":
            arguments["contract_version"] = "2.0"
        print(dumps(service.call("workspace_status", arguments)))
    finally:
        service.close()


CONFIGURED_COMMANDS = {
    "schema": run_schema,
    "call": run_call,
    "migrate": run_migrate,
    "serve": run_serve,
    "status": run_status,
}
