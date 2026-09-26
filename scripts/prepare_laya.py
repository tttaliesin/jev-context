"""Explicit, revision-pinned download. Never imported by the inference server."""

import hashlib
import json
import urllib.request
from pathlib import Path

REVISION = "1c5edc17a7acd8701df6fc341c0d179f1c62c982"
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".local/models/laya-multilingual"


def main():
    url = f"https://huggingface.co/api/models/convaiinnovations/laya/tree/{REVISION}/multilingual?recursive=true"
    with urllib.request.urlopen(url, timeout=60) as response:
        items = json.load(response)
    manifest = {"repository": "convaiinnovations/laya", "revision": REVISION, "files": {}}
    for item in items:
        if item["type"] != "file":
            continue
        relative = Path(item["path"]).relative_to("multilingual")
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid remote path")
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        expected_hash = item.get("lfs", {}).get("oid")
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".partial")
            download = (
                f"https://huggingface.co/convaiinnovations/laya/resolve/{REVISION}/{item['path']}"
            )
            with (
                urllib.request.urlopen(download, timeout=120) as response,
                temporary.open("wb") as out,
            ):
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            if temporary.stat().st_size != item["size"]:
                raise ValueError("Download size mismatch")
            with temporary.open("rb") as downloaded:
                if (
                    expected_hash
                    and hashlib.file_digest(downloaded, "sha256").hexdigest() != expected_hash
                ):
                    raise ValueError("Download hash mismatch")
            temporary.replace(target)
        with target.open("rb") as source:
            sha = hashlib.file_digest(source, "sha256").hexdigest()
        if target.stat().st_size != item["size"] or (expected_hash and sha != expected_hash):
            raise ValueError("Existing model differs from pinned revision")
        manifest["files"][relative.as_posix()] = sha
        print(f"Verified {relative}: {target.stat().st_size} bytes", flush=True)
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    profile_path = ROOT / ".local/laya-profile.json"
    if not profile_path.exists():
        profile = {
            "family": "laya",
            "model_revision": REVISION + ":multilingual",
            "implementation_revision": "573e5b62696ba441230cd6be71d593331b5d23af",
            "precision": "float32-cpu",
            "template_revision": "jev-context-v2-1",
            "sampling": "deterministic",
            "score_definition": "choice distribution; confidence = 1 - normalized Shannon entropy, not correctness probability",
            "model_path": str(DEST),
            "python": str(ROOT / ".local/laya-venv/Scripts/python.exe"),
            "device": "cpu",
            "prepare_timeout_seconds": 90,
            "lock_root": str(ROOT / ".local/model-locks"),
            "cpu_threads": 4,
            "warmup": True,
            "manifest_sha256": hashlib.sha256((DEST / "manifest.json").read_bytes()).hexdigest(),
        }
        profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
