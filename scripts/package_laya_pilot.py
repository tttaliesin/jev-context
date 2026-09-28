"""Package a same-architecture Laya checkpoint in an isolated Ollaya store; parity required."""

import argparse
import copy
import hashlib
import json
import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def header(path):
    with Path(path).open("rb") as stream:
        size = struct.unpack("<Q", stream.read(8))[0]
        if size > 16 * 1024 * 1024:
            raise ValueError("Unexpected header")
        raw = stream.read(size)
    return size + 8, json.loads(raw), raw


def tensor(path, name):
    import numpy as np

    base, metadata, _ = header(path)
    info = metadata[name]
    dtype = {"F16": "<f2", "F32": "<f4"}[info["dtype"]]
    return np.memmap(
        path,
        mode="r",
        dtype=dtype,
        offset=base + info["data_offsets"][0],
        shape=tuple(info["shape"]),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-store", type=Path, default=ROOT / ".local/ollaya-evaluation/models")
    parser.add_argument("--pilot", type=Path, default=ROOT / ".local/laya-finetuning/pilot")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/laya-finetuning/ollaya-store")
    args = parser.parse_args()
    import numpy as np
    import onnx
    from onnx import numpy_helper

    source = args.base_store
    manifest_path = source / "manifests/ollaya.dev/library/laya/multilingual"
    manifest = json.loads(manifest_path.read_text())
    report = json.loads((args.pilot / "train.json").read_text(encoding="utf-8"))
    checkpoint = args.pilot / "checkpoint/model.safetensors"
    if report["status"] != "completed" or sha(checkpoint) != report["checkpoint_sha256"]:
        raise ValueError("Training did not complete or checkpoint changed")
    weight_layer = next(x for x in manifest["layers"] if x["mediaType"].endswith(".weights"))
    original = source / "blobs" / weight_layer["digest"].replace(":", "-")
    original_offset, original_header, raw_header = header(original)
    if raw_header != header(checkpoint)[2] or original.stat().st_size != checkpoint.stat().st_size:
        raise ValueError("Original storage layout changed")
    for layer in [manifest["config"], *manifest["layers"]]:
        file = source / "blobs" / layer["digest"].replace(":", "-")
        if sha(file) != layer["digest"].split(":")[1] or file.stat().st_size != layer["size"]:
            raise ValueError("Base model integrity mismatch")
    destination = args.output.resolve()
    if not destination.is_relative_to(ROOT / ".local/laya-finetuning"):
        raise ValueError("Only the isolated fine-tuning directory is allowed")
    destination.mkdir(parents=True, exist_ok=False)
    blobs = destination / "blobs"
    blobs.mkdir()

    def blob_bytes(payload, media, annotations=None):
        h = hashlib.sha256(payload).hexdigest()
        (blobs / ("sha256-" + h)).write_bytes(payload)
        item = {"mediaType": media, "digest": "sha256:" + h, "size": len(payload)}
        if annotations:
            item["annotations"] = annotations
        return item

    new_hash = sha(checkpoint)
    new_location = "sha256-" + new_hash
    shutil.copyfile(checkpoint, blobs / new_location)
    new_weights = {
        "mediaType": weight_layer["mediaType"],
        "digest": "sha256:" + new_hash,
        "size": checkpoint.stat().st_size,
    }
    graph_layer = next(
        x
        for x in manifest["layers"]
        if x["mediaType"].endswith("graph.onnx")
        and x.get("annotations", {}).get("org.ollaya.precision") == "fp32"
    )
    graph = onnx.load(
        source / "blobs" / graph_layer["digest"].replace(":", "-"), load_external_data=False
    )
    external, inline = set(), {}
    for value in graph.graph.initializer:
        if not value.external_data:
            continue
        info = {item.key: item.value for item in value.external_data}
        name = value.name.removeprefix("w:")
        meta = original_header.get(name)
        if (
            not meta
            or info["location"] != weight_layer["digest"].replace(":", "-")
            or int(info["offset"]) != original_offset + meta["data_offsets"][0]
            or int(info["length"]) != meta["data_offsets"][1] - meta["data_offsets"][0]
            or list(value.dims) != meta["shape"]
            or value.data_type != {"F16": 10, "F32": 1}.get(meta["dtype"])
        ):
            raise ValueError(f"Unverified external tensor layout: {value.name}")
        for entry in value.external_data:
            if entry.key == "location":
                entry.value = new_location
        external.add(name)
    for name in set(report["changed_tensors"]) - external:
        old, new = tensor(original, name), tensor(checkpoint, name)
        matches = []
        for value in graph.graph.initializer:
            if value.external_data:
                continue
            current = numpy_helper.to_array(value)
            for transform, before, after in [("identity", old, new), ("transpose", old.T, new.T)]:
                if current.shape == before.shape and np.array_equal(
                    current, before.astype(current.dtype)
                ):
                    matches.append((value, transform, after.astype(current.dtype).copy()))
        if len(matches) != 1:
            raise ValueError(f"Cannot uniquely map changed inline tensor: {name}")
        value, transform, replacement = matches[0]
        inline[name] = {"initializer": value.name, "transform": transform}
        value.CopyFrom(numpy_helper.from_array(replacement, name=value.name))
    new_graph = blob_bytes(
        graph.SerializeToString(), graph_layer["mediaType"], graph_layer["annotations"]
    )
    onnx.checker.check_model(str(blobs / new_graph["digest"].replace(":", "-")))
    layers = [new_graph, new_weights]
    for layer in manifest["layers"]:
        if layer["mediaType"].rsplit(".", 1)[-1] not in {
            "tokenizer",
            "decision",
            "calibration",
            "license",
        }:
            continue
        retained = copy.deepcopy(layer)
        retained.pop("urls", None)
        filename = retained["digest"].replace(":", "-")
        shutil.copyfile(source / "blobs" / filename, blobs / filename)
        layers.append(retained)
    config = json.loads(
        (source / "blobs" / manifest["config"]["digest"].replace(":", "-")).read_text()
    )
    config.update(
        description="Local Jev head-training pilot; not calibrated or approved for production",
        source="local-checkpoint-sha256:" + new_hash,
    )
    new_config = blob_bytes(json.dumps(config).encode(), manifest["config"]["mediaType"])
    target = destination / "manifests/ollaya.dev/library/jev-laya/pilot"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": manifest["mediaType"],
                "config": new_config,
                "layers": layers,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = {
        "status": "packaged_requires_runtime_parity",
        "model": "jev-laya:pilot",
        "base_manifest_sha256": sha(manifest_path),
        "manifest_sha256": sha(target),
        "checkpoint_sha256": new_hash,
        "external_tensors": len(external),
        "inline_updated": inline,
        "promotion_eligible": False,
        "onnx": onnx.__version__,
    }
    (args.pilot / "package.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
