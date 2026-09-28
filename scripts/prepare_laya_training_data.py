"""Read-only project-memory inventory and explicit human-label preparation. Never infer gold labels."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "relevance": ["relevant", "irrelevant", "insufficient_evidence"],
    "evidence_relation": [
        "supports",
        "contradicts",
        "partial",
        "unrelated",
        "insufficient_evidence",
    ],
    "capability_fit": ["fit", "unfit", "insufficient_evidence"],
}
VERIFICATION_MARKER = "data_usage:verification_only"


def verification_only(scope):
    return VERIFICATION_MARKER in scope.get("constraints", [])


def verification_works(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='works'").fetchone():
        return set()
    return {
        wid
        for wid, raw in db.execute("SELECT id,body FROM works")
        if verification_only(json.loads(raw).get("scope", {}))
    }


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def jsonl(path, rows):
    Path(path).write_text(
        "".join(canonical(r) + "\n" for r in rows), encoding="utf-8", newline="\n"
    )


def inventory(db_path):
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    candidates, seen = [], set()
    stats = Counter()
    try:
        excluded = verification_works(db)
        for packet_id, work_id, raw in db.execute(
            "SELECT id,work_id,body FROM packets ORDER BY id"
        ):
            packet = json.loads(raw)
            stats["packets"] += 1
            if work_id in excluded or verification_only(packet.get("scope", {})):
                stats["verification_packets_excluded"] += 1
                continue
            evaluations = packet.get("judgment", {}).get("evaluations", [])
            stats["past_evaluations"] += len(evaluations)
            stats["evaluations_without_original_state"] += sum(
                "state" not in e for e in evaluations
            )
            goal = packet.get("scope", {}).get("goal")
            if not goal:
                continue
            for source in packet.get("evidence", []):
                if not source.get("text") or not source.get("source_id"):
                    continue
                state = {
                    "query": goal,
                    "goal": goal,
                    "constraints": [],
                    "candidate": source["text"],
                }
                key = digest(state)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "id": "review-" + key[:20],
                        "purpose": "relevance",
                        "state": state,
                        "expected": None,
                        "critical": None,
                        "review": {"status": "pending", "reviewer": None, "reviewed_at": None},
                        "provenance": "reconstructed_goal_source_pair",
                        "original_model_request": False,
                        "work_id": work_id,
                        "packet_id": packet_id,
                        "source_refs": [
                            {
                                k: source[k]
                                for k in (
                                    "source_id",
                                    "revision",
                                    "locator",
                                    "start_line",
                                    "end_line",
                                )
                                if k in source
                            }
                        ],
                        "source_text_sha256": hashlib.sha256(source["text"].encode()).hexdigest(),
                    }
                )
        stats["review_candidates"] = len(candidates)
        stats["human_reviewed"] = 0
        stats["target_candidates"] = 600
        return candidates, dict(stats)
    finally:
        db.close()


def audit_records(db_path):
    """Keep exact recorded observations separate from reconstructed evidence questions."""
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("BEGIN")
    rows, stats = [], Counter()

    def base(identity, work_id, state, provenance, refs, record):
        return {
            "id": identity,
            "work_id": work_id,
            "state": state,
            "state_sha256": digest(state),
            "source_refs": refs,
            "source_record": record,
            "source_record_sha256": digest(record),
            "source_text_sha256": digest(state["candidate"]),
            "provenance": provenance,
            "expected": None,
            "critical": None,
            "review": {"status": "pending", "reviewer": None, "reviewed_at": None},
            "automatic_checks": {"record_read_from_project_db": True, "gold_label_verified": False},
            "eligible_for_independent_test": False,
            "exposure": "development_audit",
        }

    try:
        excluded = verification_works(db)
        for pid, wid, raw in db.execute("SELECT id,work_id,body FROM packets ORDER BY id"):
            packet = json.loads(raw)
            stats["packets"] += 1
            if wid in excluded or verification_only(packet.get("scope", {})):
                stats["verification_packets_excluded"] += 1
                stats["verification_evaluations_excluded"] += len(
                    packet.get("judgment", {}).get("evaluations", [])
                )
                continue
            for index, result in enumerate(packet.get("judgment", {}).get("evaluations", [])):
                stats["past_evaluations"] += 1
                request = result.get("request")
                if not request:
                    stats["evaluations_without_original_request"] += 1
                    continue
                if not result.get("request_dispatched"):
                    stats["requests_not_dispatched"] += 1
                    continue
                if digest(request["state"]) != result.get("input_hash"):
                    stats["invalid_input_hash"] += 1
                    continue
                refs = [result["source_ref"]] if result.get("source_ref") else []
                refs.append({"source_id": pid, "locator": "project-db:" + pid})
                for question in request["questions"]:
                    purpose = question["purpose"]
                    if purpose not in LABELS:
                        continue
                    row = base(
                        f"actual-{pid}-{index}-{purpose}",
                        wid,
                        request["state"],
                        "captured_model_request",
                        refs,
                        result,
                    )
                    row.update(purpose=purpose, original_model_request=True, packet_id=pid)
                    rows.append(row)
                    stats["captured_request_cases"] += 1
        for eid, wid, raw in db.execute("SELECT id,work_id,body FROM evidence ORDER BY id"):
            record = json.loads(raw)
            stats["evidence_records"] += 1
            if wid in excluded:
                stats["verification_evidence_excluded"] += 1
                continue
            observation = record.get("observation")
            if not record.get("claim") or not isinstance(observation, dict):
                stats["incomplete_evidence_records"] += 1
                continue
            if not observation.get("command") or not observation.get("result_excerpt"):
                stats["incomplete_evidence_records"] += 1
                continue
            refs = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT locator FROM source_refs WHERE owner_type='evidence' AND owner_id=?",
                    (eid,),
                )
            ]
            refs.append({"source_id": eid, "locator": "project-db:" + eid})
            state = {
                "context": "프로젝트에 저장된 과거 실행 보고다. 아래 기록이 당시 주장을 뒷받침하는지 판단한다. 기록 자체의 독립적인 사실 확인이나 현재 상태 확인을 뜻하지 않는다.",
                "query": record["claim"],
                "claim": record["claim"],
                "candidate": canonical(observation),
            }
            row = base("record-" + eid, wid, state, "reconstructed_record_pair", refs, record)
            row.update(
                purpose="evidence_relation",
                original_model_request=False,
                recorded_provenance=record.get("provenance"),
                evidence_id=eid,
            )
            rows.append(row)
            stats["reconstructed_evidence_cases"] += 1
        stats["cases"] = len(rows)
        stats["connected_groups"] = len(set(assign_groups(rows)))
        return rows, {
            **stats,
            "purpose_counts": dict(Counter(r["purpose"] for r in rows)),
            "human_reviewed": 0,
            "quality_evaluation": False,
        }
    finally:
        db.close()


def assign_groups(rows):
    """Connect all shared work IDs, source locators, and exact source text before splitting."""
    parents = {}

    def find(key):
        parents.setdefault(key, key)
        if parents[key] != key:
            parents[key] = find(parents[key])
        return parents[key]

    keys_by_row = []
    for row in rows:
        keys = ["id:" + row["id"], "text:" + digest(row["state"]["candidate"])]
        if row.get("work_id"):
            keys.append("work:" + row["work_id"])
        if row.get("pair_group_id"):
            keys.append("pair:" + row["pair_group_id"])
        if row.get("derived_from"):
            keys.append("id:" + row["derived_from"])
        for ref in row.get("source_refs", []):
            keys.append("source-id:" + ref["source_id"])
            if ref.get("locator"):
                keys.append("source-locator:" + ref["locator"])
        raw = row.get("automatic_checks", {}).get("raw_observation_check", {})
        for ref in raw.get("files", []):
            if ref.get("path"):
                keys.append("source-locator:" + ref["path"])
            if ref.get("sha256"):
                keys.append("raw-file:" + ref["sha256"])
        for key in keys[1:]:
            a, b = find(keys[0]), find(key)
            parents[max(a, b)] = min(a, b)
        keys_by_row.append(keys)
    groups = {}
    for keys in keys_by_row:
        groups.setdefault(find(keys[0]), []).extend(keys)
    group_ids = {root: digest(sorted(set(keys))) for root, keys in groups.items()}
    return [group_ids[find(keys[0])] for keys in keys_by_row]


def retain_reviews(rows, previous):
    """Carry review/exposure forward only for the exact same recorded case."""
    old = {r["id"]: r for r in previous}
    if len(old) != len(previous):
        raise ValueError("Duplicate previous review IDs")
    count = 0
    for row in rows:
        prior = old.get(row["id"])
        if prior is None:
            continue
        for field in ("state", "source_record"):
            if digest(prior[field]) != row[field + "_sha256"]:
                raise ValueError(f"Previous {field} changed: {row['id']}")
        if any(prior.get(k) != row.get(k) for k in ("purpose", "work_id", "source_refs")):
            raise ValueError(f"Previous provenance changed: {row['id']}")
        for field in (
            "review",
            "expected",
            "critical",
            "agent_review",
            "automatic_checks",
            "exposure",
            "eligible_for_training",
            "eligible_for_independent_test",
            "usage",
        ):
            if field in prior:
                row[field] = prior[field]
        count += 1
    return count


def prepare_reviewed(rows, minimum=600):
    errors = []
    if len(rows) < minimum:
        errors.append(f"Need {minimum} reviewed candidates; received {len(rows)}")
    if len({r["id"] for r in rows}) != len(rows):
        errors.append("Duplicate row IDs")
    exposed = set()
    for name in ("development", "validation"):
        for case in json.loads(
            (ROOT / f"evaluations/ollaya-tuning/{name}.json").read_text("utf-8")
        )["cases"]:
            exposed.add(digest(case["state"]))
    rubric = ROOT / "evaluations/laya-finetuning/review-draft-20260928.json"
    for case in json.loads(rubric.read_text("utf-8"))["cases"]:
        exposed.add(digest(case["state"]))
    for row in rows:
        state = row["state"]
        if (
            row.get("usage") in {"rubric_review_only", "verification_only"}
            or row.get("eligible_for_training") is False
            or verification_only(state)
            or verification_only(state.get("scope", {}))
        ):
            errors.append(f"Review-only case: {row['id']}")
        if row.get("provenance") not in {"captured_model_request", "reconstructed_record_pair"}:
            errors.append(f"Actual record provenance missing: {row['id']}")
        if row.get("state_sha256") != digest(row["state"]):
            errors.append(f"Original input missing/changed: {row['id']}")
        record = row.get("source_record")
        if not record or digest(record) != row.get("source_record_sha256"):
            errors.append(f"Original record missing/changed: {row['id']}")
        review = row.get("review", {})
        if (
            review.get("status") != "human_reviewed"
            or not review.get("reviewer")
            or not review.get("reviewed_at")
            or not review.get("reason")
        ):
            errors.append(f"Human review missing: {row['id']}")
        if row.get("expected") not in LABELS.get(row.get("purpose"), []):
            errors.append(f"Gold label missing/invalid: {row['id']}")
        if not isinstance(row.get("critical"), bool) or not row.get("source_refs"):
            errors.append(f"Critical flag or provenance missing: {row['id']}")
        if digest(row["state"]) in exposed:
            errors.append(f"Previously exposed diagnostic: {row['id']}; keep in pilot only")
    prepared = []
    for row, group in zip(rows, assign_groups(rows), strict=True):
        bucket = int(group[:8], 16) % 100
        split = (
            "train"
            if bucket < 70
            else "development"
            if bucket < 80
            else "calibration"
            if bucket < 90
            else "test"
        )
        prepared.append({**row, "group_id": group, "split": split})
    counts = Counter((r["split"], r["purpose"]) for r in prepared)
    distribution = {
        "split_purpose_label_counts": {
            f"{split}/{purpose}/{label}": count
            for (split, purpose, label), count in sorted(
                Counter(
                    (r["split"], r["purpose"], r.get("expected") or "unreviewed") for r in prepared
                ).items()
            )
        },
        "split_purpose_group_counts": {
            f"{split}/{purpose}": len(
                {r["group_id"] for r in prepared if r["split"] == split and r["purpose"] == purpose}
            )
            for split, purpose in sorted(counts)
        },
    }
    exposed_groups = {
        r["group_id"] for r in prepared if r.get("eligible_for_independent_test") is False
    }
    for row in prepared:
        if row["split"] == "test" and row["group_id"] in exposed_groups:
            errors.append(f"Development-exposed group cannot be heldout: {row['id']}")
    for split in ("train", "development", "calibration", "test"):
        for purpose in LABELS:
            if counts[split, purpose] == 0:
                errors.append(f"No independent group for {split}/{purpose}")
    for purpose in LABELS:
        if counts["test", purpose] < 30:
            errors.append(
                f"Need 30 heldout cases for {purpose}; received {counts['test', purpose]}"
            )
    if errors:
        return {
            "status": "not_ready",
            **distribution,
            "errors": errors,
            "groups": len({r["group_id"] for r in prepared}),
            "split_purpose_counts": {
                f"{split}/{purpose}": count for (split, purpose), count in sorted(counts.items())
            },
        }, []
    return {
        "status": "ready",
        **distribution,
        "rows": len(rows),
        "data_sha256": digest(prepared),
        "groups": len({r["group_id"] for r in prepared}),
        "split_purpose_counts": {
            f"{split}/{purpose}": count for (split, purpose), count in sorted(counts.items())
        },
    }, prepared


def write_review_html(path, rows):
    template = (ROOT / "scripts/laya_training_review.html").read_text(encoding="utf-8")
    payload = canonical({"rows": rows, "labels": LABELS}).replace("<", "\\u003c")
    path.write_text(template.replace("/*REVIEW_DATA*/", payload), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["inventory", "audit", "prepare"])
    parser.add_argument("--db", type=Path)
    parser.add_argument("--reviewed", type=Path)
    parser.add_argument(
        "--previous-review", type=Path, help="Preserve exact-case review/exposure on audit"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.stage in {"inventory", "audit"}:
        if not args.db:
            parser.error("inventory needs --db")
        rows, stats = (inventory if args.stage == "inventory" else audit_records)(args.db)
        if args.previous_review:
            if args.stage != "audit":
                parser.error("--previous-review requires audit")
            previous = [
                json.loads(line)
                for line in args.previous_review.read_text("utf-8").splitlines()
                if line
            ]
            stats["reviews_retained"] = retain_reviews(rows, previous)
        stats["human_reviewed"] = sum(
            r.get("review", {}).get("status") == "human_reviewed" for r in rows
        )
        jsonl(args.output / "review-candidates.jsonl", rows)
        write_review_html(args.output / "review.html", rows)
        stats["connected_groups"] = len(set(assign_groups(rows)))
        stats["purpose_counts"] = dict(Counter(r["purpose"] for r in rows))
        stats["note"] = (
            "Provenance is per case. Recorded outcomes and predictions are not gold labels."
        )
    else:
        if not args.reviewed:
            parser.error("prepare needs --reviewed")
        rows = [json.loads(line) for line in args.reviewed.read_text("utf-8").splitlines() if line]
        stats, prepared = prepare_reviewed(rows)
        if prepared:
            target = args.output / "frozen.jsonl"
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write("".join(canonical(r) + "\n" for r in prepared))
    (args.output / (args.stage + ".json")).write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in stats.items() if k != "errors"}, ensure_ascii=False))
    if stats.get("status") == "not_ready":
        print(f"{len(stats['errors'])} readiness failures; see prepare.json")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
