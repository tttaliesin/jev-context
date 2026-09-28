"""Explicit, isolated diagnostic. Never changes the production profile or promotion policy."""

import argparse
import copy
import hashlib
import http.client
import ipaddress
import json
import math
import statistics
import subprocess
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from jev_context.engines import SemifOpenVINO, convert

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluations/ollaya"
TEMPERATURES = [0.5, 0.75, 1, 1.5, 2, 3, 4]
THRESHOLDS = [0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
BASE_MODEL = "laya:multilingual"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process_snapshot(root_pid):
    """Windows working-set sample of this owned process tree, not a peak or GPU measurement."""
    root_pid = int(root_pid)
    command = (
        f"$taskIds = [System.Collections.Generic.HashSet[int]]::new(); $null=$taskIds.Add({root_pid}); "
        "$taskAll = @(Get-CimInstance Win32_Process); "
        "do { $taskChanged=$false; foreach($taskP in $taskAll) { "
        "if($taskIds.Contains([int]$taskP.ParentProcessId)) { "
        "if($taskIds.Add([int]$taskP.ProcessId)) { $taskChanged=$true } } } } while($taskChanged); "
        "@($taskAll | Where-Object { $taskIds.Contains([int]$_.ProcessId) } | "
        "Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    rows = rows if isinstance(rows, list) else [rows]
    return {"processes": rows, "working_set_sum_bytes": sum(r["WorkingSetSize"] for r in rows)}


def fixtures():
    frozen = read(FIXTURES / "frozen.json")
    for name, expected in frozen["files"].items():
        if sha(FIXTURES / name) != expected:
            raise ValueError(f"Frozen fixture changed: {name}")
    return read(FIXTURES / "cases.json")["cases"], read(FIXTURES / "questions.json"), frozen


class Client:
    def __init__(self, endpoint):
        address = urlsplit(endpoint)
        if (
            address.scheme != "http"
            or not ipaddress.ip_address(address.hostname).is_loopback
            or address.username
            or address.password
            or address.path not in ("", "/")
            or address.query
            or address.fragment
        ):
            raise ValueError("A literal HTTP loopback endpoint is required")
        self.host, self.port = address.hostname, address.port or 80

    def call(self, path, body=None, timeout=10):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            connection.request(
                "GET" if body is None else "POST",
                path,
                None if body is None else json.dumps(body, ensure_ascii=False).encode(),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            value = json.loads(response.read())
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}: {json.dumps(value)}")
            return value
        finally:
            connection.close()

    def unload(self, model):
        result = self.call("/api/decide", {"model": model, "keep_alive": 0}, timeout=60)
        if result.get("done_reason") != "unload" or self.call("/api/ps")["models"]:
            raise RuntimeError("Model unload was not confirmed")
        return result


def scale(probabilities, temperature):
    if temperature <= 0 or not math.isfinite(temperature):
        raise ValueError("Temperature must be finite and positive")
    values = {key: max(value, 1e-8) ** (1 / temperature) for key, value in probabilities.items()}
    total = sum(values.values())
    return {key: value / total for key, value in values.items()}


def metrics(rows, threshold=0):
    observed = [r for r in rows if r.get("probabilities")]
    accepted = [
        r
        for r in observed
        if r["choice"] != "insufficient_evidence" and max(r["probabilities"].values()) >= threshold
    ]
    latency = sorted(r["latency_ms"] for r in observed)
    return {
        "count": len(rows),
        "observed": len(observed),
        "correct": sum(r.get("choice") == r["expected"] for r in rows),
        "critical_errors": sum(r["critical"] and r.get("choice") != r["expected"] for r in rows),
        "errors": sum(r["status"] == "error" for r in rows),
        "not_run": sum(r["status"] == "not_run" for r in rows),
        "accepted": len(accepted),
        "accepted_correct": sum(r["choice"] == r["expected"] for r in accepted),
        "critical_wrong_accepts": sum(
            r["critical"] and r["choice"] != r["expected"] for r in accepted
        ),
        "nll_observed": statistics.mean(
            -math.log(max(r["probabilities"][r["expected"]], 1e-8)) for r in observed
        )
        if observed
        else None,
        "brier_observed": statistics.mean(
            sum((p - int(k == r["expected"])) ** 2 for k, p in r["probabilities"].items())
            for r in observed
        )
        if observed
        else None,
        "p50_ms": statistics.median(latency) if latency else None,
        "p95_ms": latency[math.ceil(len(latency) * 0.95) - 1] if latency else None,
        "within_2s": sum(r["latency_ms"] <= 2000 for r in observed),
        "within_6s": sum(r["latency_ms"] <= 6000 for r in observed),
        "confusion": dict(
            Counter(f"{r['purpose']}:{r['expected']}->{r.get('choice', r['status'])}" for r in rows)
        ),
    }


def select(development):
    # Only development rows enter selection; fail rather than fit a failed/incomplete run.
    if any(
        r["split"] != "development" or r["status"] != "observed"
        for rows in development.values()
        for r in rows
    ):
        raise ValueError("Selection needs complete development observations only")
    variant = max(
        development,
        key=lambda v: (
            metrics(development[v])["correct"],
            -metrics(development[v])["critical_errors"],
            v == "baseline",
        ),
    )
    rows = development[variant]
    candidates = []
    for temperature in TEMPERATURES:
        calibrated = [{**r, "probabilities": scale(r["probabilities"], temperature)} for r in rows]
        candidates.append((metrics(calibrated)["nll_observed"], temperature, calibrated))
    _, temperature, calibrated = min(candidates, key=lambda item: (item[0], abs(item[1] - 1)))
    threshold = 1.01  # Explicit accept-none result.
    for candidate in THRESHOLDS:
        report = metrics(calibrated, candidate)
        if (
            report["accepted"] >= 10
            and report["accepted_correct"] / report["accepted"] >= 0.9
            and report["critical_wrong_accepts"] == 0
        ):
            threshold = candidate
            break
    return {
        "variant": variant,
        "relative_temperature": temperature,
        "threshold": threshold,
        "development_metrics": metrics(calibrated, threshold),
    }


def question_contract(purpose, wire):
    return {
        "id": purpose,
        "purpose": purpose,
        "language": "ko",
        "instructions": wire["instructions"],
        "options": wire["criteria"],
    }


def run_rows(cases, evaluator, checkpoint):
    rows, failures = [], 0
    for case in cases:
        row = {key: case[key] for key in ("id", "split", "purpose", "expected", "critical")}
        if failures >= 3:
            row.update(status="not_run", reason="three_consecutive_errors")
        else:
            started = time.perf_counter()
            try:
                answer = evaluator(case)
                row.update(
                    status="observed",
                    choice=answer["choice"],
                    probabilities=answer["raw_distribution"],
                    raw_confidence=answer["raw_confidence"],
                )
                failures = 0
            except Exception as exc:
                row.update(status="error", reason=str(exc))
                failures += 1
            row["latency_ms"] = (time.perf_counter() - started) * 1000
        rows.append(row)
        save(checkpoint, rows)
        print(f"{checkpoint.stem} {case['id']} {row['status']} {row.get('choice', '')}", flush=True)
    return rows


def ollaya_rows(client, model, cases, questions, checkpoint):
    if client.call("/api/ps")["models"]:
        raise RuntimeError("Unload the previous model before this run")
    started = time.perf_counter()
    load = client.call("/api/decide", {"model": model, "keep_alive": -1}, timeout=300)
    preparation = time.perf_counter() - started

    def evaluate(case):
        purpose = case["purpose"]
        raw = client.call(
            "/v1/systemone",
            {"model": model, "state": case["state"], "questions": {purpose: questions[purpose]}},
        )
        if raw.get("state_truncated"):
            raise ValueError("Truncated state")
        if raw.get("model") != model:
            raise ValueError("Unexpected model identity")
        return convert(
            raw,
            [question_contract(purpose, questions[purpose])],
            "Ollaya normalized maximum probability",
        )[0]

    try:
        rows = run_rows(cases, evaluate, checkpoint)
        residency = client.call("/api/ps")
    finally:
        client.unload(model)
    return {
        "preparation_seconds": preparation,
        "load": load,
        "residency": residency,
        "first_request_ms": rows[0].get("latency_ms"),
        "metrics": metrics(rows),
        "rows": rows,
    }


def identity(model_store):
    manifest = model_store / "manifests/ollaya.dev/library/laya/multilingual"
    value = read(manifest)
    calibration = None
    hashes = {"manifest": sha(manifest)}
    for layer in [value["config"], *value["layers"]]:
        blob = model_store / "blobs" / layer["digest"].replace(":", "-")
        actual = sha(blob)
        if actual != layer["digest"].split(":")[1] or blob.stat().st_size != layer["size"]:
            raise ValueError(f"Model integrity mismatch: {blob.name}")
        hashes[blob.name] = actual
        if layer["mediaType"] == "application/vnd.ollaya.calibration":
            calibration = read(blob)
    if calibration is None:
        raise ValueError("Base calibration is unavailable")
    lock = read(FIXTURES / "model-lock.json")
    if hashes != lock["hashes"]:
        raise ValueError("Registry model changed since the recorded experiment")
    return {"hashes": hashes, "calibration": calibration}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["develop", "validate", "semif", "contracts", "resources"])
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11437")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/ollaya-evaluation/results")
    parser.add_argument(
        "--model-store", type=Path, default=ROOT / ".local/ollaya-evaluation/models"
    )
    parser.add_argument("--profile", type=Path, default=ROOT / ".local/semif-ov-profile.json")
    args = parser.parse_args()
    cases, questions, frozen = fixtures()
    client = Client(args.endpoint)
    output = args.output
    stage_path = output / f"{args.stage}.json"
    if stage_path.exists():
        raise ValueError("Existing run is immutable; use a new --output directory")
    output.mkdir(parents=True, exist_ok=True)
    # Reserve even interrupted stages: a retry needs a separate output directory.
    with (output / f"{args.stage}.started").open("x", encoding="utf-8") as marker:
        marker.write(str(time.time()))
    report = {
        "stage": args.stage,
        "fixtures": frozen,
        "promotion_eligible": False,
        "dataset_provenance": "agent_authored_diagnostic",
    }
    if args.stage != "semif":
        model = identity(args.model_store)
        report["model"] = model
        report["version"] = client.call("/api/version")
        if report["version"]["version"] != read(FIXTURES / "model-lock.json")["version"]:
            raise ValueError("Ollaya binary version changed")
    if args.stage in {"develop", "validate"}:
        if args.stage == "develop":
            subset = [c for c in cases if c["split"] == "development"]
            report["runs"] = {
                variant: ollaya_rows(
                    client, BASE_MODEL, subset, wire, output / f"development-{variant}.json"
                )
                for variant, wire in questions.items()
            }
            choice = select({name: run["rows"] for name, run in report["runs"].items()})
            calibration = copy.deepcopy(model["calibration"])
            base_temperature = calibration.get("temperature_by_options", {}).get(
                "choice:3-5", calibration["temperature"][0]
            )
            calibration.setdefault("temperature_by_options", {})["choice:3-5"] = (
                base_temperature * choice["relative_temperature"]
            )
            choice.update(
                calibration=calibration,
                base_temperature=base_temperature,
                model_hashes=model["hashes"],
                fixtures=frozen,
            )
            save(output / "selection.json", choice)
            save(output / "calibration.json", calibration)
            save(output / "selected-questions.json", questions[choice["variant"]])
            (output / "Modelfile").write_text(
                "FROM laya:multilingual\nQUESTIONS ./selected-questions.json\nCALIBRATION ./calibration.json\nPARAMETER precision fp32\nDESCRIPTION Jev Korean diagnostic only; not approved for production\n",
                encoding="utf-8",
            )
            report["selection"] = choice
        else:
            choice = read(output / "selection.json")
            if choice != read(output / "develop.json")["selection"]:
                raise ValueError("Development selection changed")
            if choice["fixtures"] != frozen or choice["model_hashes"] != model["hashes"]:
                raise ValueError("Selection identity changed")
            report["selection_sha256"] = sha(output / "selection.json")
            subset = [c for c in cases if c["split"] == "validation"]
            report["runs"] = {}
            for name, variant, model_name in [
                ("baseline", "baseline", BASE_MODEL),
                ("selected", choice["variant"], BASE_MODEL),
                ("calibrated", choice["variant"], "jev-diagnostic:latest"),
            ]:
                if name == "calibrated":
                    report["create"] = client.call(
                        "/api/create",
                        {
                            "model": model_name,
                            "from": BASE_MODEL,
                            "questions": questions[variant],
                            "calibration": choice["calibration"],
                            "parameters": {"precision": "fp32"},
                            "stream": False,
                        },
                    )
                run = ollaya_rows(
                    client,
                    model_name,
                    subset,
                    questions[variant],
                    output / f"validation-{name}.json",
                )
                run["metrics_with_development_threshold"] = metrics(
                    run["rows"], choice["threshold"]
                )
                report["runs"][name] = run
            differences = []
            for original, calibrated in zip(
                report["runs"]["selected"]["rows"],
                report["runs"]["calibrated"]["rows"],
                strict=True,
            ):
                if original.get("probabilities") and calibrated.get("probabilities"):
                    predicted = scale(original["probabilities"], choice["relative_temperature"])
                    differences.append(
                        max(abs(v - calibrated["probabilities"][k]) for k, v in predicted.items())
                    )
            report["calibration_probability_max_difference"] = max(differences, default=None)
    elif args.stage == "semif":
        if client.call("/api/ps")["models"]:
            raise RuntimeError("Ollaya must be unloaded first")
        profile = read(args.profile)
        engine = SemifOpenVINO(profile)
        started = time.perf_counter()
        try:
            engine.prepare()
            report.update(
                preparation_seconds=time.perf_counter() - started,
                fingerprint=engine.fingerprint,
                startup=engine.startup,
            )

            def evaluate(case):
                return engine.evaluate(
                    {
                        "evaluation_id": case["id"],
                        "profile_fingerprint": engine.fingerprint,
                        "deadline_ms": 10000,
                        "state": case["state"],
                        "questions": [
                            question_contract(
                                case["purpose"], questions["baseline"][case["purpose"]]
                            )
                        ],
                    }
                )["answers"][0]

            report["rows"] = run_rows(
                [c for c in cases if c["split"] == "validation"],
                evaluate,
                output / "validation-semif.json",
            )
            report["metrics"] = metrics(report["rows"])
        finally:
            engine.close()
    elif args.stage == "resources":
        if args.server_pid is None:
            raise ValueError("resources requires the owned --server-pid")
        if client.call("/api/ps")["models"]:
            raise RuntimeError("Resource samples require no loaded model")
        report["server_idle"] = process_snapshot(args.server_pid)
        try:
            client.call("/api/decide", {"model": BASE_MODEL, "keep_alive": -1}, timeout=300)
            report["ollaya_loaded"] = process_snapshot(args.server_pid)
        finally:
            client.unload(BASE_MODEL)
        report["server_after_unload"] = process_snapshot(args.server_pid)
        engine = SemifOpenVINO(read(args.profile))
        try:
            engine.prepare()
            report["semif_loaded"] = process_snapshot(engine.process.pid)
            case = next(c for c in cases if c["split"] == "validation")
            report["semif_fixed_sample_repetition_ms"] = []
            for index in range(3):
                started = time.perf_counter()
                engine.evaluate(
                    {
                        "evaluation_id": f"resource-{index}",
                        "profile_fingerprint": engine.fingerprint,
                        "deadline_ms": 10000,
                        "state": case["state"],
                        "questions": [
                            question_contract(
                                case["purpose"], questions["baseline"][case["purpose"]]
                            )
                        ],
                    }
                )
                report["semif_fixed_sample_repetition_ms"].append(
                    (time.perf_counter() - started) * 1000
                )
            report["semif_after_inference"] = process_snapshot(engine.process.pid)
            worker_pid = engine.process.pid
        finally:
            engine.close()
        report["semif_after_close"] = process_snapshot(worker_pid)
    else:
        purpose = "relevance"
        report["initial_ps"] = client.call("/api/ps")
        if report["initial_ps"]["models"]:
            raise RuntimeError("Contracts require an idle server")
        client.call("/api/decide", {"model": BASE_MODEL, "keep_alive": -1}, timeout=300)
        try:
            try:
                client.call(
                    "/v1/systemone",
                    {
                        "model": BASE_MODEL,
                        "state": "근거 자료 " * 3000,
                        "questions": {purpose: questions["baseline"][purpose]},
                    },
                    timeout=30,
                )
                report["oversize_rejected"] = False
            except RuntimeError as exc:
                report["oversize_error"] = str(exc)
                report["oversize_rejected"] = "422" in str(exc) and "STATE_TRUNCATED" in str(exc)
            repetitions = []
            case = next(c for c in cases if c["split"] == "validation")
            for _ in range(3):
                started = time.perf_counter()
                client.call(
                    "/v1/systemone",
                    {
                        "model": BASE_MODEL,
                        "state": case["state"],
                        "questions": {purpose: questions["baseline"][purpose]},
                    },
                )
                repetitions.append((time.perf_counter() - started) * 1000)
            report["fixed_sample_repetition_ms"] = repetitions
            client.call("/api/decide", {"model": BASE_MODEL, "keep_alive": "1s"})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and client.call("/api/ps")["models"]:
                time.sleep(0.25)
            report["idle_unloaded"] = not client.call("/api/ps")["models"]
        finally:
            report["explicit_unload"] = client.unload(BASE_MODEL)
    save(stage_path, report)
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k in {"stage", "metrics", "selection", "oversize_rejected", "idle_unloaded"}
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
