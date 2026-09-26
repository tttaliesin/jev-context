"""Versioned OpenVINO cache directories; original and unrelated caches are never removed."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def openvino_environment():
    """Record every inherited OpenVINO override, including internal plugin settings."""
    return {name: os.environ[name] for name in sorted(os.environ) if name.startswith("OV_")}


def cache_namespace(cache_root, model_path, ov, core, device, compile_options):
    """Compute a namespace without compiling or writing, also usable for verified cache seeding."""
    model_path = Path(model_path)
    manifest = model_path / "manifest.json"
    if manifest.is_file():
        model_identity = {"manifest_sha256": _file_hash(manifest)}
    else:
        # Older profiles have no manifest. Hash every input used by this fixed IR adapter.
        names = (
            "model.xml",
            "model.bin",
            "static.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "chat_template.jinja",
            "config.json",
            "special_tokens_map.json",
            "added_tokens.json",
        )
        model_identity = {
            "files": {
                name: _file_hash(model_path / name)
                for name in names
                if (model_path / name).is_file()
            }
        }
    properties = {}
    for name in (
        "FULL_DEVICE_NAME",
        "DEVICE_ARCHITECTURE",
        "DRIVER_VERSION",
        "GPU_DRIVER_VERSION",
        "DEVICE_UUID",
        "DEVICE_LUID",
        "DEVICE_ID",
    ):
        try:
            properties[name] = str(core.get_property(device, name))
        except (AttributeError, RuntimeError):
            # Property support differs by plugin/runtime. Missing data is explicit in the key.
            properties[name] = None
    version = ov.get_version() if hasattr(ov, "get_version") else ov.__version__
    identity = {
        "format": 1,
        "model": model_identity,
        "openvino_version": str(version),
        "device": device,
        "device_properties": properties,
        "compile_options": compile_options,
        "runtime_environment": openvino_environment(),
        "cache_mode": "OPTIMIZE_SPEED (OpenVINO default)",
    }
    key = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
    namespace = Path(cache_root).expanduser().resolve() / "jev-openvino-v1" / key
    _check_namespace(namespace)
    return namespace, identity


def _check_namespace(namespace):
    namespace = Path(namespace)
    if (
        namespace.parent.name != "jev-openvino-v1"
        or len(namespace.name) != 64
        or any(c not in "0123456789abcdef" for c in namespace.name)
        or namespace.parent.resolve() != namespace.parent
        or namespace.resolve() != namespace
    ):
        raise ValueError("Unsafe managed OpenVINO cache namespace")


def prepare_cache(namespace, identity):
    """Create only the managed namespace and record its complete cache identity."""
    _check_namespace(namespace)
    namespace.mkdir(parents=True, exist_ok=True)
    marker = namespace / "identity.json"
    contents = _json(identity)
    try:
        with marker.open("x", encoding="utf-8") as stream:
            stream.write(contents)
    except FileExistsError:
        if marker.read_text(encoding="utf-8") != contents:
            raise ValueError("OpenVINO cache identity changed") from None


def quarantine_cache(namespace):
    """Retain a failed namespace for diagnosis; never rename the user-supplied cache root."""
    _check_namespace(namespace)
    # Keep rejection paths shorter than the active 64-character namespace on Windows.
    # The retained identity.json records the original model/runtime identity.
    rejected = namespace.with_name(f"rejected-{uuid.uuid4().hex}")
    namespace.rename(rejected)
    return rejected
