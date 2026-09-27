"""Project-bounded paths and atomic writes shared by setup and runtime observations."""

import os
from pathlib import Path

from .common import DomainError, uid


def safe_path(root, relative):
    root = Path(root).resolve(strict=True)
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise DomainError("unsafe_path", "설정 경로가 선택한 프로젝트 밖을 가리킵니다.")
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise DomainError("unsafe_path", "설정 경로의 바로가기·연결 폴더를 사용할 수 없습니다.")
    return target


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uid("tmp"))
    try:
        with temporary.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
