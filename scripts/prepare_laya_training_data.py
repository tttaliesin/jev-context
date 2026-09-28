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
        for packet_id, work_id, raw in db.execute(
            "SELECT id,work_id,body FROM packets ORDER BY id"
        ):
            packet = json.loads(raw)
            stats["packets"] += 1
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
        for ref in row.get("source_refs", []):
            keys.append("source:" + ref.get("locator", ref["source_id"]))
        for key in keys[1:]:
            a, b = find(keys[0]), find(key)
            parents[max(a, b)] = min(a, b)
        keys_by_row.append(keys)
    groups = {}
    for keys in keys_by_row:
        groups.setdefault(find(keys[0]), []).extend(keys)
    group_ids = {root: digest(sorted(set(keys))) for root, keys in groups.items()}
    return [group_ids[find(keys[0])] for keys in keys_by_row]


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
    for row in rows:
        review = row.get("review", {})
        if (
            review.get("status") != "human_reviewed"
            or not review.get("reviewer")
            or not review.get("reviewed_at")
        ):
            errors.append(f"Human review missing: {row['id']}")
        if row.get("expected") not in LABELS.get(row.get("purpose"), []):
            errors.append(f"Gold label missing/invalid: {row['id']}")
        if not isinstance(row.get("critical"), bool) or not row.get("source_refs"):
            errors.append(f"Critical flag or provenance missing: {row['id']}")
        if digest(row["state"]) in exposed:
            errors.append(f"Previously exposed diagnostic: {row['id']}; keep in pilot only")
    if errors:
        return {"status": "not_ready", "errors": errors}, []
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
    for split in ("train", "development", "calibration", "test"):
        for purpose in LABELS:
            if counts[split, purpose] == 0:
                errors.append(f"No independent group for {split}/{purpose}")
    if errors:
        return {"status": "not_ready", "errors": errors}, []
    return {"status": "ready", "rows": len(rows), "data_sha256": digest(prepared)}, prepared


def write_review_html(path, rows):
    template = (ROOT / "scripts/laya_training_review.html").read_text(encoding="utf-8")
    payload = canonical({"rows": rows, "labels": LABELS}).replace("<", "\\u003c")
    path.write_text(template.replace("/*REVIEW_DATA*/", payload), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["inventory", "prepare"])
    parser.add_argument("--db", type=Path)
    parser.add_argument("--reviewed", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.stage == "inventory":
        if not args.db:
            parser.error("inventory needs --db")
        rows, stats = inventory(args.db)
        jsonl(args.output / "review-candidates.jsonl", rows)
        write_review_html(args.output / "review.html", rows)
        stats["connected_groups"] = len(set(assign_groups(rows)))
        stats["purpose_counts"] = dict(Counter(r["purpose"] for r in rows))
        stats["note"] = "Reconstructed review candidates, not original requests or gold labels"
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
