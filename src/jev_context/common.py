from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime


class DomainError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        self.code, self.message, self.retryable = code, message, retryable
        super().__init__(message)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def dumps(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_loads(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def invalid(_):
        raise ValueError("non-finite JSON number")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=invalid)


def response(request_id, data=None, outcome="ok", warnings=None, error=None):
    return dict(
        contract_version="1.0",
        request_id=request_id,
        outcome=outcome,
        data=data or {},
        warnings=warnings or [],
        error=error,
    )


def failed(request_id, error):
    return response(
        request_id,
        outcome="conflict" if error.code == "revision_conflict" else "error",
        error=dict(code=error.code, message=error.message, retryable=error.retryable),
    )
