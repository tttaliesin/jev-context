"""Materialize only the 48 fixed rank-3 zero-weight paths, without compiling any device."""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--write", action="store_true", help="Serialize the inspected candidate")
    args = parser.parse_args()
    started = time.perf_counter()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    source = Path(profile["model_path"]).resolve(strict=True)
    output = args.output.resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("The candidate must be separate from the original model directory")
    profile_path = Path(str(output) + ".profile.json")
    if output.exists() or profile_path.exists():
        raise ValueError("Refusing to overwrite an existing candidate or profile")
    manifest_path = source / "manifest.json"
    source_manifest_sha = sha256(manifest_path)
    if source_manifest_sha != profile["manifest_sha256"]:
        raise ValueError("Original manifest does not match the pinned profile")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = (source / relative).resolve(strict=True)
        if not path.is_relative_to(source) or sha256(path) != expected:
            raise ValueError(f"Original model file failed verification: {relative}")

    import numpy as np
    import openvino as ov
    from openvino import opset13 as ops

    # read_model and Model.evaluate use IR loading/reference evaluation, never a GPU plugin.
    model = ov.Core().read_model(source / "model.xml")
    expected_names = {
        f"__module.body.layers.{layer}.linear_attn/aten::bmm/MatMul{suffix}"
        for layer in range(32)
        if layer % 4 != 3
        for suffix in ("", "_1")
    }
    targets = [
        node for node in model.get_ordered_ops() if node.get_friendly_name() in expected_names
    ]
    if len(targets) != 48 or {node.get_friendly_name() for node in targets} != expected_names:
        raise ValueError("The pinned 48-node pattern is not present")
    tensors, replacements, target_records = {}, {}, []
    for node in targets:
        if node.get_type_name() != "MatMul" or node.get_attributes() != {
            "transpose_a": False,
            "transpose_b": False,
        }:
            raise ValueError("Unexpected target MatMul operation")
        convert = node.input_value(1).get_node()
        if (
            convert.get_type_name() != "Convert"
            or convert.get_output_element_type(0) != ov.Type.f32
        ):
            raise ValueError("Expected the original f16-to-f32 weight conversion")
        multiply = convert.input_value(0).get_node()
        if (
            multiply.get_type_name() != "Multiply"
            or list(multiply.get_output_shape(0)) != [32, 128, 128]
            or multiply.get_output_element_type(0) != ov.Type.f16
        ):
            raise ValueError("Unexpected dequantized weight tensor")
        subtract, scale = (multiply.input_value(i).get_node() for i in range(2))
        if subtract.get_type_name() != "Subtract" or scale.get_type_name() != "Constant":
            raise ValueError("Expected Subtract then Multiply dequantization")
        weight_convert, zp_convert = (subtract.input_value(i).get_node() for i in range(2))
        if any(
            part.get_type_name() != "Convert" or part.get_output_element_type(0) != ov.Type.f16
            for part in (weight_convert, zp_convert)
        ):
            raise ValueError("Expected u8-to-f16 weight/zero-point conversion")
        weight, zp = (part.input_value(0).get_node() for part in (weight_convert, zp_convert))
        if any(
            part.get_type_name() != "Constant" or part.get_output_element_type(0) != ov.Type.u8
            for part in (weight, zp)
        ):
            raise ValueError("Expected original u8 constants")
        if (
            list(weight.get_output_shape(0)) != [32, 128, 128]
            or list(zp.get_output_shape(0)) != [32, 1, 128]
            or list(scale.get_output_shape(0)) != [32, 1, 128]
        ):
            raise ValueError("Unexpected compressed weight/scale/zero-point shape")
        key = multiply.get_friendly_name()
        if key not in tensors:
            tensor = ov.Tensor(ov.Type.f16, multiply.get_output_shape(0))
            reference = ov.Model([multiply.output(0)], [])
            if not reference.evaluate([tensor], []):
                raise ValueError("OpenVINO reference dequantization failed")
            data = tensor.data.copy()
            # Narrow this migration to the observed fixed zero states, including sign bits.
            if np.any(weight.get_data()) or np.any(zp.get_data()) or np.any(data.view(np.uint16)):
                raise ValueError("The inspected all-positive-zero weight invariant changed")
            constant = ops.constant(data)
            constant.set_friendly_name(key + "/materialized_f16")
            if constant.get_data().tobytes() != data.tobytes():
                raise ValueError("Constant construction changed f16 reference bits")
            tensors[key] = {
                "f16_sha256": hashlib.sha256(data.tobytes()).hexdigest(),
                "bytes": data.nbytes,
                "all_positive_zero": True,
                "scale_min": float(scale.get_data().min()),
                "scale_max": float(scale.get_data().max()),
                "constant_name": constant.get_friendly_name(),
            }
            replacements[key] = constant
        target_records.append({"matmul": node.get_friendly_name(), "weight": key})
    if len(tensors) != 24:
        raise ValueError("Expected exactly 24 unique dequantization subgraphs")
    report = {
        "source_manifest_sha256": source_manifest_sha,
        "source_files": manifest["files"],
        "script_sha256": sha256(Path(__file__)),
        "openvino_version": ov.get_version(),
        "method": "OpenVINO Model.evaluate of original f16 Convert/Subtract/Multiply; keep f32 Convert",
        "target_count": len(targets),
        "unique_weight_count": len(tensors),
        "materialized_bytes": sum(item["bytes"] for item in tensors.values()),
        "original_bin_bytes": (source / "model.bin").stat().st_size,
        "weights": tensors,
        "targets": target_records,
        "full_model_gpu_equivalence": "not_tested",
    }
    if not args.write:
        print(json.dumps(report, ensure_ascii=False))
        return
    # Replace only the target's weight input. Preserve its original f32 Convert, MatMul,
    # activation path, precision, tokenizer, and static input contract.
    for node in targets:
        convert = node.input_value(1).get_node()
        original = convert.input_value(0).get_node()
        if original.get_type_name() == "Multiply":
            convert.input(0).replace_source_output(
                replacements[original.get_friendly_name()].output(0)
            )
    model.validate_nodes_and_infer_types()
    output.mkdir(parents=True)
    ov.serialize(model, output / "model.xml", output / "model.bin")
    for relative in manifest["files"]:
        if relative not in {"model.xml", "model.bin"}:
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
    # Reload and check the serialized target constants, without compiling any device.
    reloaded = ov.Core().read_model(output / "model.xml")
    by_name = {node.get_friendly_name(): node for node in reloaded.get_ordered_ops()}
    for record in target_records:
        convert = by_name[record["matmul"]].input_value(1).get_node()
        constant = convert.input_value(0).get_node()
        if (
            constant.get_type_name() != "Constant"
            or constant.get_output_element_type(0) != ov.Type.f16
            or hashlib.sha256(constant.get_data().tobytes()).hexdigest()
            != tensors[record["weight"]]["f16_sha256"]
        ):
            raise ValueError("Serialized f16 target failed reference-bit verification")
    report.update(
        serialized_reference_bits_verified=True,
        candidate_bin_bytes=(output / "model.bin").stat().st_size,
        preparation_seconds=time.perf_counter() - started,
    )
    write_json(output / "materialization.json", report)
    derived_manifest = {
        "source": manifest["source"],
        "derivation": {
            key: report[key]
            for key in (
                "source_manifest_sha256",
                "script_sha256",
                "openvino_version",
                "method",
                "target_count",
            )
        },
        "files": {
            str(path.relative_to(output)).replace("\\", "/"): sha256(path)
            for path in sorted(output.rglob("*"))
            if path.is_file()
        },
    }
    write_json(output / "manifest.json", derived_manifest)
    candidate_profile = {
        **profile,
        "model_path": str(output),
        "manifest_sha256": sha256(output / "manifest.json"),
        "implementation_revision": profile["implementation_revision"] + "; rank3-zero-f16-v1",
        "cache_dir": str(Path(__file__).resolve().parents[1] / ".local/model-cache/semif-openvino"),
    }
    write_json(profile_path, candidate_profile)
    print(
        json.dumps(
            {
                "model_path": str(output),
                "profile": str(profile_path),
                "manifest_sha256": candidate_profile["manifest_sha256"],
                "candidate_bin_bytes": report["candidate_bin_bytes"],
                "preparation_seconds": report["preparation_seconds"],
            }
        )
    )


if __name__ == "__main__":
    main()
