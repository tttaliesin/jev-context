"""Project-scoped desktop setup. No model preparation or automatic source collection."""

from __future__ import annotations

import base64
import contextlib
import copy
import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path

from .cli import codex_entry
from .common import DomainError, digest, dumps, now, uid
from .policy import Config
from .project_files import atomic_write, safe_path

TARGETS = (".codex/config.toml", ".agents/skills/jev-context/SKILL.md")
STATE = ".local/jev-setup"
SKILL = Path(__file__).resolve().parents[2] / "skills/jev-context/SKILL.md"


def read_bytes(path):
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise DomainError("invalid_file", "설정 파일이 아니거나 크기 제한을 초과했습니다.")
    return path.read_bytes()


def stamp(value):
    return digest(value) if value is not None else None


@contextlib.contextmanager
def locked(root):
    path = safe_path(root, STATE + "/setup.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        if path.stat().st_size == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DomainError(
                "setup_busy", "다른 설정 작업이 진행 중입니다. 잠시 후 재시도하세요."
            ) from exc
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)


def journal(root):
    content = read_bytes(safe_path(root, STATE + "/last-install.json"))
    return json.loads(content) if content else None


def write_journal(root, value):
    atomic_write(safe_path(root, STATE + "/last-install.json"), dumps(value).encode())


def preview(config_path):
    config_path = Path(config_path).resolve(strict=True)
    config = Config.load(config_path)
    if config.read_only:
        raise DomainError("read_only", "읽기 전용 프로젝트에는 연결 설정을 설치할 수 없습니다.")
    root = config.project_root
    settings, skill = [safe_path(root, name) for name in TARGETS]
    before = [read_bytes(settings), read_bytes(skill)]
    old = (before[0] or b"").decode("utf-8-sig")
    parsed = tomllib.loads(old)
    if not isinstance(parsed.get("mcp_servers", {}), dict):
        raise DomainError("invalid_toml", "MCP 서버 설정이 TOML 테이블 형식이 아닙니다.")
    entry = codex_entry(config_path)
    entry["cwd"] = str(root)
    # The service is installed in the selected Python environment; cwd is the user's project.
    entry["env"] = {"PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONUTF8": "1"}
    addition = (
        "\n[mcp_servers.jev_context]\n"
        + "\n".join(
            f"{key} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in entry.items()
            if key != "env"
        )
        + "\n[mcp_servers.jev_context.env]\n"
        + "\n".join(
            f"{key} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in entry["env"].items()
        )
        + "\n"
    )
    existing = parsed.get("mcp_servers", {}).get("jev_context")
    updated = old
    if existing != entry:
        if existing is not None:
            pattern = r"(?ms)^\[mcp_servers\.jev_context(?:\.[^\]\n]+)?\][^\n]*\n.*?(?=^\[|\Z)"
            updated, count = re.subn(pattern, "", old)
            if not count:
                raise DomainError(
                    "unsupported_toml", "Jev 설정을 보존하며 갱신할 수 없는 TOML 형식입니다."
                )
        updated += addition
        expected = copy.deepcopy(parsed)
        expected.setdefault("mcp_servers", {})["jev_context"] = entry
        if tomllib.loads(updated) != expected:
            raise DomainError("unsupported_toml", "다른 설정을 보존할 수 없어 적용을 중단했습니다.")
    desired = [before[0] if existing == entry else updated.encode(), SKILL.read_bytes()]
    fingerprint = digest(
        dumps(
            [
                str(config_path),
                stamp(config_path.read_bytes()),
                [stamp(v) for v in before],
                [stamp(v) for v in desired],
            ]
        ).encode()
    )
    files = [
        {
            "path": str(path),
            "relative_path": relative,
            "action": "keep" if old_bytes == new else "create" if old_bytes is None else "update",
            "before_hash": stamp(old_bytes),
            "after_hash": stamp(new),
        }
        for path, relative, old_bytes, new in zip(
            (settings, skill), TARGETS, before, desired, strict=True
        )
    ]
    return (
        {
            "fingerprint": fingerprint,
            "project_root": str(root),
            "config_path": str(config_path),
            "files": files,
            "entry_preview": addition.strip(),
            "skill_preview": desired[1].decode(),
            "installed": all(f["action"] == "keep" for f in files),
        },
        before,
        desired,
    )


def status(config_path):
    path = Path(config_path)
    result = {
        "environment": {"python": sys.executable, "version": sys.version.split()[0]},
        "config_path": str(path),
        "project_ready": False,
        "installed": False,
        "can_restore": False,
        "confirmation": {"state": "not_requested"},
    }
    if not path.is_file():
        return result
    config = Config.load(path)
    result.update(
        project_ready=config.db_path.is_file(),
        project_root=str(config.project_root),
        allowed_paths=list(config.allowed_paths),
        model_mode=config.engine.get("state"),
    )
    previous = journal(config.project_root)
    result["can_restore"] = bool(previous and previous.get("status") in {"applied", "prepared"})
    result["recovery_pending"] = bool(previous and previous.get("status") == "prepared")
    try:
        plan, _, _ = preview(path)
        result["installed"] = plan["installed"]
        result["confirmation"] = confirmation_status(config, plan["fingerprint"])
    except (ValueError, DomainError) as exc:
        result["configuration_error"] = str(exc)[:400]
    return result


def create_project(root, allowed):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or root == root.parent or root == Path.home():
        raise DomainError("invalid_project", "작업할 프로젝트 폴더를 선택하세요.")
    if (
        not isinstance(allowed, list)
        or len(allowed) > 30
        or any(
            not isinstance(v, str)
            or not v
            or len(v) > 240
            or ".." in v
            or ":" in v
            or v.startswith(("/", "\\"))
            for v in allowed
        )
    ):
        raise DomainError("invalid_paths", "수집 범위는 프로젝트 안의 상대 경로로 입력하세요.")
    path = safe_path(root, ".local/project.toml")
    with locked(root):
        if not path.exists():
            content = {
                "project_root": str(root),
                "data_root": str(safe_path(root, ".local/state")),
                "project_id": uid("project"),
                "contract_version": "2.0",
                "allowed_paths": allowed,
            }
            data = "\n".join(
                f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in content.items()
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as file:
                file.write(data + '\n[engine]\nstate = "disabled"\n')
        config = Config.load(path)
        if config.project_root != root:
            raise DomainError("project_mismatch", "기존 설정이 다른 프로젝트를 가리킵니다.")
        from .service import Service

        service = Service(config)
        service.close()
    return status(path)


def initialize_existing(config_path):
    config = Config.load(config_path)
    if config.read_only:
        if not config.db_path.is_file():
            raise DomainError("read_only", "읽기 전용 프로젝트의 작업 DB가 없습니다.")
        return status(config_path)
    from .service import Service

    with locked(config.project_root):
        service = Service(config)
        service.close()
    return status(config_path)


def install(config_path, fingerprint):
    config = Config.load(config_path)
    root = config.project_root
    with locked(root):
        plan, before, desired = preview(config_path)
        if plan["fingerprint"] != fingerprint:
            raise DomainError(
                "stale_preview", "설정이 변경됐습니다. 변경 내용 다시 보기를 눌러 주세요."
            )
        previous = journal(root)
        if previous and previous.get("status") == "prepared":
            raise DomainError(
                "recovery_pending", "중단된 설치가 있습니다. 먼저 설정 되돌리기를 실행하세요."
            )
        if plan["installed"]:
            return status(config_path)
        backup = {"id": uid("install"), "created_at": now(), "status": "prepared", "files": []}
        for item, old, new in zip(plan["files"], before, desired, strict=True):
            if old != new:
                backup["files"].append(
                    {
                        "relative_path": item["relative_path"],
                        "before": base64.b64encode(old).decode() if old is not None else None,
                        "after_hash": stamp(new),
                    }
                )
        # Retain previous backups as well as the latest recovery point.
        atomic_write(safe_path(root, STATE + "/" + backup["id"] + ".json"), dumps(backup).encode())
        write_journal(root, backup)
        written = []
        try:
            for item, old, new in zip(plan["files"], before, desired, strict=True):
                path = safe_path(root, item["relative_path"])
                if read_bytes(path) != old:
                    raise DomainError(
                        "concurrent_change", "설치 중 파일이 바뀌었습니다. 다시 확인하세요."
                    )
                if old != new:
                    atomic_write(path, new)
                    written.append((path, old, new))
            backup["status"] = "applied"
            write_journal(root, backup)
        except Exception:
            restored = True
            for path, old, new in reversed(written):
                try:
                    if read_bytes(path) != new:
                        restored = False
                    elif old is None:
                        path.unlink()
                    else:
                        atomic_write(path, old)
                except OSError:
                    restored = False
            backup["status"] = "rolled_back" if restored else "prepared"
            write_journal(root, backup)
            raise
        invalidate_confirmation(config)
    return status(config_path)


def restore(config_path):
    config = Config.load(config_path)
    if config.read_only:
        raise DomainError("read_only", "읽기 전용 프로젝트의 연결 설정을 변경할 수 없습니다.")
    root = config.project_root
    with locked(root):
        backup = journal(root)
        if not backup or backup.get("status") not in {"applied", "prepared"}:
            raise DomainError("no_backup", "되돌릴 연결 설정이 없습니다.")
        entries = []
        for item in backup["files"]:
            if item["relative_path"] not in TARGETS:
                raise DomainError("invalid_backup", "올바른 연결 백업이 아닙니다.")
            path = safe_path(root, item["relative_path"])
            old = (
                base64.b64decode(item["before"], validate=True)
                if item["before"] is not None
                else None
            )
            current = read_bytes(path)
            if stamp(current) not in {item["after_hash"], stamp(old)}:
                raise DomainError(
                    "restore_conflict",
                    "설치 후 파일이 수정되어 덮어쓸 수 없습니다. 변경 내용을 보존했습니다.",
                )
            entries.append((path, old, current))
        # Journal remains recoverable if a process stops between these writes.
        backup["status"] = "prepared"
        write_journal(root, backup)
        for path, old, current in entries:
            if read_bytes(path) != current:
                raise DomainError(
                    "restore_conflict", "복원 중 파일이 변경됐습니다. 다시 확인하세요."
                )
            if old is None:
                path.unlink(missing_ok=True)
            elif current != old:
                atomic_write(path, old)
        backup["status"] = "restored"
        write_journal(root, backup)
        invalidate_confirmation(config)
    return status(config_path)


def confirmation_path(config):
    return safe_path(config.project_root, STATE + "/confirmation.json")


def invalidate_confirmation(config):
    confirmation_path(config).unlink(missing_ok=True)


def confirmation_status(config, fingerprint):
    content = read_bytes(confirmation_path(config))
    if not content:
        return {"state": "not_requested"}
    item = json.loads(content)
    if item.get("fingerprint") != fingerprint:
        return {"state": "settings_changed"}
    if item.get("expires_at", 0) < time.time():
        return {"state": "expired"}
    return {**item, "state": "received" if item.get("received_at") else "waiting"}


def challenge(config_path):
    config = Config.load(config_path)
    with locked(config.project_root):
        plan, _, _ = preview(config_path)
        if not plan["installed"]:
            raise DomainError("not_installed", "먼저 Codex 연결 설정을 적용하세요.")
        args = {"request_id": uid("jev-connect")}
        if config.contract_version == "2.0":
            args["contract_version"] = "2.0"
        item = {
            "fingerprint": plan["fingerprint"],
            "request_id": args["request_id"],
            "expires_at": time.time() + 600,
            "created_at": now(),
            "prompt": "이 프로젝트의 jev_context MCP workspace_status 도구를 다음 인자로 직접 호출해 줘. "
            "셸이나 CLI로 대신 실행하지 말고, 도구가 없으면 연결이 필요하다고 알려줘.\n"
            + dumps(args),
        }
        atomic_write(confirmation_path(config), dumps(item).encode())
    return status(config_path)


def observe_confirmation(
    config, name, arguments, result, client_name, config_path, loaded_hash=None
):
    """Called only by the MCP transport, never by Service/CLI/desktop health checks.

    Client names are self-reported. This records a challenge response, not host identity.
    """
    if name != "workspace_status" or result.get("outcome") != "ok" or config.read_only:
        return
    if not isinstance(client_name, str) or "codex" not in client_name.lower():
        return
    if not str(arguments.get("request_id", "")).startswith("jev-connect-"):
        return
    try:
        if loaded_hash and stamp(Path(config_path).read_bytes()) != loaded_hash:
            return
        with locked(config.project_root):
            plan, _, _ = preview(config_path)
            item = confirmation_status(config, plan["fingerprint"])
            if item["state"] != "waiting" or item.get("request_id") != arguments["request_id"]:
                return
            item.update(received_at=now(), client_name=client_name[:120])
            atomic_write(confirmation_path(config), dumps(item).encode())
    except (OSError, ValueError, DomainError):
        pass  # Optional setup receipt cannot fail an otherwise valid workspace read.


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        if sys.version_info[:2] != (3, 12):
            raise DomainError("python_version", "Jev 환경의 Python 3.12 실행기를 선택하세요.")
        request = json.loads(sys.stdin.buffer.read(65537))
        action = request["action"]
        path = request.get("config_path")
        if request.get("expected_root") and path and Path(path).is_file():
            if Config.load(path).project_root != Path(request["expected_root"]).resolve(
                strict=True
            ):
                raise DomainError(
                    "project_mismatch", "기존 설정이 선택한 폴더와 다른 프로젝트를 가리킵니다."
                )
        if action == "status":
            result = status(path)
        elif action == "create":
            result = create_project(request["project_root"], request["allowed_paths"])
        elif action == "initialize":
            result = initialize_existing(path)
        elif action == "preview":
            result = preview(path)[0]
        elif action == "install":
            result = install(path, request["fingerprint"])
        elif action == "restore":
            result = restore(path)
        elif action == "challenge":
            result = challenge(path)
        else:
            raise DomainError("invalid_action", "지원하지 않는 설정 작업입니다.")
        print(dumps({"result": result}))
    except (OSError, ValueError, KeyError, TypeError, DomainError) as exc:
        print(
            dumps(
                {"error": {"code": getattr(exc, "code", "setup_failed"), "message": str(exc)[:400]}}
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
