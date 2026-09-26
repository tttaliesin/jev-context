"""One-shot Modal diagnostic; no deployment, public endpoint, or profile promotion."""

import hashlib
import json
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
OPENJEV_REVISION = "e04794ab36e4f7e6040c2547baecdb2737ce2e79"
MODEL = "nvidia/diffusiongemma-26B-A4B-it-NVFP4"
MODEL_REVISION = "ec4ff3df205028f4e81c954c2227f9312b3ec2ea"
IMAGE = (
    "razorback16/openjev@sha256:597dc873e59137e976f4ced5d5694ddb309650964d514bf0107d178a96986d28"
)
JIT_CACHE_WHEEL = (
    "https://github.com/flashinfer-ai/flashinfer/releases/download/v0.6.18.post1/"
    "flashinfer_jit_cache-0.6.18.post1+cu130-cp39-abi3-manylinux_2_28_x86_64.whl"
    "#sha256=d1729490636a98f22f158518e10c257eec5a188d3dadf30b4c9de298675d8d4b"
)
app = modal.App("jev-openjev-diagnostic", include_source=False)
cache = modal.Volume.from_name("jev-openjev-model-cache", create_if_missing=True)
download_image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "huggingface-hub==0.34.4", "hf-xet==1.1.9"
)
gpu_image = (
    modal.Image.from_registry(IMAGE, add_python="3.12")
    .entrypoint([])
    .run_commands(
        "curl -fL --retry 2 "
        f"https://github.com/razorback16/openjev/archive/{OPENJEV_REVISION}.tar.gz "
        "-o /tmp/openjev.tar.gz",
        "uv pip install --python /opt/venv/bin/python --no-deps /tmp/openjev.tar.gz",
    )
    .run_commands(
        f"uv pip install --python /opt/venv/bin/python --no-deps '{JIT_CACHE_WHEEL}'",
        '/opt/venv/bin/python -c "import importlib.util; from pathlib import Path; '
        "s=importlib.util.find_spec('flashinfer_jit_cache'); "
        "matches=list(Path(s.origin).parent.rglob('*fused_moe_120*')); "
        "assert matches, 'Missing SM120 MoE cache'; print(matches)\"",
    )
    .add_local_file(ROOT / "scripts/openjev_modal_worker.py", "/diagnostic/worker.py")
)


@app.function(
    serialized=True,
    image=download_image,
    volumes={"/cache": cache},
    cpu=(2, 2),
    memory=(4096, 4096),
    timeout=1200,
    max_containers=1,
    retries=0,
    single_use_containers=True,
)
def prepare_model():
    from huggingface_hub import snapshot_download

    path = snapshot_download(MODEL, revision=MODEL_REVISION, cache_dir="/cache/hub")
    cache.commit()
    return path


@app.function(
    serialized=True,
    image=gpu_image,
    gpu="RTX-PRO-6000",
    volumes={"/cache": cache},
    cpu=(4, 4),
    memory=(65536, 65536),
    timeout=1200,
    startup_timeout=180,
    max_containers=1,
    retries=0,
    single_use_containers=True,
)
def diagnose(model_path, dataset):
    import os
    import subprocess

    cache.reload()
    env = {
        **os.environ,
        "PATH": "/opt/venv/bin:" + os.environ["PATH"],
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HOME": "/cache",
    }
    Path("/tmp/cases.json").write_text(json.dumps(dataset, ensure_ascii=False))
    process = subprocess.run(
        ["/opt/venv/bin/python", "/diagnostic/worker.py", model_path],
        env=env,
        timeout=1170,
        check=False,
    )
    output = Path("/tmp/report.json")
    if not output.exists():
        return {"status": "failed", "worker_exit_code": process.returncode}
    report = json.loads(output.read_text())
    report["worker_exit_code"] = process.returncode
    return report


@app.local_entrypoint()
def main(output: str = ".local/evaluations/openjev-modal.json"):
    # Import only locally. Modal uploads the worker and synthetic fixture payload.
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    # Use the installed service's exact question builder instead of duplicating prompts.
    from jev_context.judgment import question

    ko_raw = (ROOT / "models/korean-diagnostic.json").read_bytes()
    en_raw = (ROOT / "models/english-diagnostic.json").read_bytes()
    ko, en = json.loads(ko_raw), json.loads(en_raw)
    cases = []
    for korean, english in zip(ko["cases"], en["cases"], strict=True):
        assert korean["expected"] == english["expected"]
        assert korean["purpose"] == english["purpose"]
        for case in (korean, english):
            q = question(case["purpose"], case["language"])
            cases.append({**case, "question": q})
    dataset = {"provenance": ko["provenance"], "split": ko["split"], "cases": cases}
    model_path = prepare_model.remote()
    print("Pinned model ready; starting one GPU diagnostic", flush=True)
    report = diagnose.remote(model_path, dataset)
    report.update(
        {
            "openjev_revision": OPENJEV_REVISION,
            "model": MODEL,
            "model_revision": MODEL_REVISION,
            "container_image": IMAGE,
            "modal_app_id": app.app_id,
            "modal_sdk": modal.__version__,
            "dataset_sha256": {
                "ko": hashlib.sha256(ko_raw).hexdigest(),
                "en": hashlib.sha256(en_raw).hexdigest(),
            },
            "dataset_provenance": dataset["provenance"],
            "split": dataset["split"],
            "promotion_eligible": False,
            "limitation": "Synthetic development fixtures; no human-reviewed heldout or host efficiency evidence",
        }
    )
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"results", "server_log_tail"}},
            ensure_ascii=False,
        )
    )
    print(f"Report: {dest.resolve()}")
    if report.get("status") != "completed":
        raise RuntimeError("GPU diagnostic failed; inspect the saved report")
