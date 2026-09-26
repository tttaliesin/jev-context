"""Materialize the pinned work-session profile; does not start or promote a model."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from modal_openjev import IMAGE, MODEL, MODEL_REVISION, OPENJEV_REVISION, ROOT

sys.path.insert(0, str(ROOT / "src"))
from jev_context.judgment import TEMPLATE_REVISION  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".local/openjev-modal-profile.json")
    args = parser.parse_args()
    profile = {
        "family": "openjev_modal",
        "model": MODEL,
        "model_revision": MODEL_REVISION,
        "implementation_revision": OPENJEV_REVISION,
        "container_image": IMAGE,
        "precision": "NVFP4-RTX-PRO-6000",
        "template_revision": TEMPLATE_REVISION,
        "sampling": {
            "steps": 1,
            "auto_max": 4,
            "auto_threshold": 0.1,
            "think": 0,
            "sequential": False,
        },
        "score_definition": "1-H(p)/ln(K); not correctness probability or authorization",
        "max_input_tokens": 4096,
        "transport": "modal-private-queues-v1",
        "session_file": str(ROOT / ".local/state/runtime/modal-session.secret.json"),
        "modal_python": str(ROOT / ".local/modal-venv/Scripts/python.exe"),
        "modal_config_file": str(ROOT / ".local/modal.toml"),
        "runtime_artifacts": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "scripts/modal_session.py",
                "scripts/openjev_session_kernel.py",
                "scripts/openjev_modal_worker.py",
                "scripts/modal_openjev.py",
            )
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
