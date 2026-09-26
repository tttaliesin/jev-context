from __future__ import annotations

import fnmatch
import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .common import DomainError, digest, dumps

DENIED_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".cache", ".ssh", ".aws"}
DENIED_NAMES = (
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*credentials*",
    "*secret*",
    "modal.toml",
    "*.sqlite*",
    "*.safetensors",
    "*.gguf",
    "*.bin",
    "*.pt",
)
DEFAULT_SUFFIXES = (
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".json",
    ".jsonl",
    ".toml",
    ".yaml",
    ".yml",
    ".log",
    ".csv",
    ".rs",
    ".go",
    ".html",
    ".css",
    ".sql",
)


@dataclass
class Config:
    project_root: Path
    data_root: Path
    project_id: str
    allowed_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    read_only: bool = False
    policy_revision: int = 1
    max_file_bytes: int = 2 * 1024 * 1024
    storage_limit_bytes: int = 512 * 1024 * 1024
    timeout_seconds: float = 5.0
    # Model judgment share of timeout_seconds; tuned to the installed engine's speed.
    judgment_seconds: float = 2.0
    engine: dict = field(default_factory=lambda: {"state": "disabled"})
    contract_version: str = "1.0"

    def __post_init__(self):
        if self.contract_version not in {"1.0", "2.0"}:
            raise ValueError("Unsupported contract version")
        self.project_root = self.project_root.resolve(strict=True)
        self.data_root = self.data_root.resolve()
        if not self.project_root.is_dir() or not self.project_id.startswith("project-"):
            raise ValueError("invalid project configuration")
        if any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in self.project_id
        ):
            raise ValueError("invalid project ID")
        if str(self.data_root).startswith("\\\\"):
            raise ValueError("network data roots are unsupported")

    @classmethod
    def load(cls, path):
        path = Path(path).resolve(strict=True)
        with open(path, "rb") as file:
            data = tomllib.load(file)
        # Installed configuration must not depend on the host process's working directory.
        for key in ("profile_file", "evaluation_file"):
            if key in data.get("engine", {}):
                data["engine"][key] = str((path.parent / data["engine"][key]).resolve())
        return cls(
            **{
                **data,
                "project_root": path.parent / data["project_root"],
                "data_root": path.parent / data["data_root"],
            }
        )

    @property
    def policy_hash(self):
        return digest(
            dumps(
                [
                    str(self.project_root),
                    self.allowed_paths,
                    self.exclude_paths,
                    self.max_file_bytes,
                ]
                + ([self.contract_version] if self.contract_version != "1.0" else [])
            ).encode()
        )

    @property
    def db_path(self):
        return self.data_root / "projects" / self.project_id / "state.sqlite"

    def check_path(self, relative: str) -> Path:
        normalized = relative.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if (
            not parts
            or normalized.startswith("/")
            or any(p in ("..", ".") for p in parts)
            or ":" in normalized
        ):
            raise DomainError("policy_denied", "Only project-relative file paths are allowed")
        if any(p.lower() in DENIED_PARTS for p in parts):
            raise DomainError("policy_denied", "Excluded directory")
        if any(fnmatch.fnmatchcase(parts[-1].lower(), p) for p in DENIED_NAMES):
            raise DomainError("policy_denied", "Excluded file pattern")
        if Path(parts[-1]).suffix.lower() not in DEFAULT_SUFFIXES:
            raise DomainError("policy_denied", "Unsupported text file type")
        if not any(fnmatch.fnmatchcase(normalized, p) for p in self.allowed_paths):
            raise DomainError("policy_denied", "File is outside the installation allowlist")
        if any(fnmatch.fnmatchcase(normalized, p) for p in self.exclude_paths):
            raise DomainError("policy_denied", "File is excluded by installation policy")
        path = self.project_root.joinpath(*parts)
        resolved = path.resolve()
        if not resolved.is_relative_to(self.project_root) or resolved.is_relative_to(
            self.data_root
        ):
            raise DomainError("policy_denied", "Resolved path is outside the allowed project")
        return path

    def read_file(self, relative: str):
        path = self.check_path(relative)
        try:
            with open(path, "rb") as file:
                final_path = opened_path(file, path)
                # Validate the handle target as well as the submitted path, including junctions.
                if not final_path.is_relative_to(self.project_root):
                    raise DomainError("policy_denied", "Opened file escaped project root")
                canonical = final_path.relative_to(self.project_root).as_posix()
                self.check_path(canonical)
                before = os.fstat(file.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise DomainError("policy_denied", "Not a regular file")
                raw = file.read(self.max_file_bytes + 1)
                after = os.fstat(file.fileno())
                # On Windows 3.12, stat() and fstat() can expose different ctime
                # semantics. Compare handles to handles, including the reopened path.
                with open(path, "rb") as current_file:
                    current = os.fstat(current_file.fileno())

                def identity(s):
                    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

                if identity(before) != identity(after) or identity(after) != identity(current):
                    raise DomainError("source_changed", "File changed while reading", True)
                if len(raw) > self.max_file_bytes:
                    raise DomainError("policy_denied", "File exceeds byte limit")
                if b"\x00" in raw:
                    raise DomainError("policy_denied", "Binary content is not supported")
                try:
                    decoded = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise DomainError("policy_denied", "File is not UTF-8") from exc
                return canonical, raw, decoded
        except FileNotFoundError as exc:
            raise DomainError("not_found", "File is missing") from exc
        except OSError as exc:
            raise DomainError("policy_denied", "File could not be read") from exc


def opened_path(file, fallback: Path) -> Path:
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        fn = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        fn.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = fn(msvcrt.get_osfhandle(file.fileno()), buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise DomainError("policy_denied", "Cannot verify opened file path")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value).resolve()
    handle = Path(f"/proc/self/fd/{file.fileno()}")
    if handle.exists():
        return handle.resolve()
    # Fail closed on platforms without a verified handle-path implementation.
    raise DomainError("policy_denied", "Opened-path verification is unavailable on this platform")
