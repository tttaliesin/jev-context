"""Read-only record inventory and explicit, attributed label preparation. Never infer labels."""

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
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


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text("utf-8").splitlines() if line]


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
        candidate = row["state"]["candidate"]
        if (
            row.get("purpose") == "capability_fit"
            and isinstance(candidate, dict)
            and candidate.get("id")
        ):
            keys.append("capability:" + candidate["id"])
        if row.get("work_id"):
            keys.append("work:" + row["work_id"])
        if row.get("pair_group_id"):
            keys.append("pair:" + row["pair_group_id"])
        if row.get("derived_from"):
            keys.append("id:" + row["derived_from"])
        for ref in row.get("source_refs", []):
            keys.append("source-id:" + ref["source_id"])
            if ref.get("sha256"):
                keys.append("raw-file:" + ref["sha256"])
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
            "reservation_id",
            "reservation_sha256",
            "reservation_error",
        ):
            if field in prior:
                row[field] = prior[field]
        count += 1
    return count


def verified_review(row):
    review = row.get("review", {})
    if not all(review.get(k) for k in ("reviewer", "reviewed_at", "reason")):
        return False
    if review.get("status") == "human_reviewed":
        return True
    if review.get("status") != "agent_verified":
        return False
    if row.get("expected") not in LABELS.get(row.get("purpose"), []) or not isinstance(
        row.get("critical"), bool
    ):
        return False
    files = row.get("automatic_checks", {}).get("raw_observation_check", {}).get("files", [])
    evidence = review.get("evidence", [])
    return (
        review.get("reviewer_type") == "agent"
        and review.get("target_model_output_used_as_label") is False
        and review.get("checked_state_sha256") == digest(row["state"])
        and review.get("checked_record_sha256") == digest(row.get("source_record"))
        and review.get("checked_label") == row.get("expected")
        and review.get("checked_critical") is row.get("critical")
        and bool(evidence)
        and all(
            isinstance(ref, dict)
            and ref.get("path")
            and isinstance(ref.get("sha256"), str)
            and len(ref["sha256"]) == 64
            and all(c in "0123456789abcdef" for c in ref["sha256"])
            and any(
                f.get("path") == ref["path"] and f.get("sha256") == ref["sha256"] for f in files
            )
            for ref in evidence
        )
    )


def utc_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return parsed


def reserve_test(db_path, specs, previous):
    """Reserve metadata before the first packet/evidence; never read source contents."""
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("BEGIN")
    scopes = []
    try:
        for spec in specs:
            wid = spec["work_id"]
            capabilities = spec.get("capability_sources", [])
            if not spec.get("id") or not (spec.get("source_ids") or capabilities):
                raise ValueError("Reservation needs ID and explicit source IDs")
            found = db.execute("SELECT body FROM works WHERE id=?", (wid,)).fetchone()
            if not found or verification_only(json.loads(found[0]).get("scope", {})):
                raise ValueError("Reservation needs a real, non-verification work")
            for table in ("packets", "evidence"):
                if db.execute(f"SELECT 1 FROM {table} WHERE work_id=?", (wid,)).fetchone():
                    raise ValueError("Work already has observations; cannot reserve retroactively")
            refs = []
            for sid in spec.get("source_ids", []):
                source = db.execute(
                    "SELECT s.locator,s.current_revision,r.sha256 FROM sources s "
                    "JOIN source_revisions r ON s.current_revision=r.id "
                    "WHERE s.id=? AND s.status='available'",
                    (sid,),
                ).fetchone()
                if not source:
                    raise ValueError("Reservation source missing or inactive")
                if db.execute("SELECT 1 FROM source_refs WHERE source_id=?", (sid,)).fetchone():
                    raise ValueError("Source already used; cannot reserve retroactively")
                if db.execute(
                    "SELECT 1 FROM source_refs f JOIN source_revisions r "
                    "ON f.source_id=r.source_id AND f.revision=r.id WHERE r.sha256=?",
                    (source[2],),
                ).fetchone():
                    raise ValueError("Duplicate source content already used")
                refs.append(
                    dict(source_id=sid, locator=source[0], revision=source[1], sha256=source[2])
                )
            for capability in capabilities:
                if (
                    not all(capability.get(k) for k in ("id", "version", "sha256"))
                    or len(capability["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in capability["sha256"])
                ):
                    raise ValueError("Capability reservation needs ID/version/exact candidate hash")
                if any(
                    r.get("purpose") == "capability_fit"
                    and isinstance(r["state"]["candidate"], dict)
                    and r["state"]["candidate"].get("id") == capability["id"]
                    for r in previous
                ):
                    raise ValueError("Capability source already exposed in development")
            probe = {
                "id": "reservation-" + spec["id"],
                "work_id": wid,
                "state": {"candidate": "reservation-" + spec["id"]},
                "source_refs": refs,
            }
            groups = assign_groups([*previous, probe])
            if groups[-1] in groups[:-1]:
                raise ValueError("Reservation overlaps development history")
            scopes.append(
                {
                    "id": spec["id"],
                    "work_id": wid,
                    "source_refs": refs,
                    "capability_sources": capabilities,
                }
            )
        if (
            not scopes
            or len({s["id"] for s in scopes}) != len(scopes)
            or len({s["work_id"] for s in scopes}) != len(scopes)
        ):
            raise ValueError("Empty or duplicate reservation scopes")
        body = {
            "version": 1,
            "reserved_at": datetime.now(UTC).isoformat(),
            "previous_sha256": digest(previous),
            "scopes": scopes,
        }
        return {**body, "sha256": digest(body)}
    finally:
        db.close()


def validate_reservation(manifest):
    if manifest.get("sha256") != digest({k: v for k, v in manifest.items() if k != "sha256"}):
        raise ValueError("Reservation hash changed")
    utc_time(manifest["reserved_at"])
    return manifest


def reservation_for(row, manifest):
    if not manifest or row.get("provenance") != "captured_model_request":
        return None
    validate_reservation(manifest)
    record = row.get("source_record", {})
    try:
        if utc_time(record["captured_at"]) <= utc_time(manifest["reserved_at"]):
            return None
    except (KeyError, ValueError, TypeError):
        return None
    ref = record.get("source_ref")
    for scope in manifest["scopes"]:
        if scope["work_id"] != row.get("work_id"):
            continue
        if ref and any(
            all(ref.get(k) == s[k] for k in ("source_id", "revision")) for s in scope["source_refs"]
        ):
            return scope["id"]
        candidate = row["state"]["candidate"]
        if (
            row.get("purpose") == "capability_fit"
            and isinstance(candidate, dict)
            and any(
                c["id"] == candidate.get("id")
                and c["version"] == candidate.get("version")
                and c["sha256"] == digest(candidate)
                for c in scope.get("capability_sources", [])
            )
        ):
            return scope["id"]
    return None


def apply_reservations(rows, manifest, previous, previously_seen=()):
    validate_reservation(manifest)
    if digest(previous) != manifest["previous_sha256"]:
        # History can grow, but must contain every original identity/input; callers
        # supply the same immutable reservation-time audit for admission.
        raise ValueError("Reservation requires its exact development history")
    previous_ids = {
        r["id"] for r in [*previous, *previously_seen] if r.get("exposure") != "test_reserved"
    }
    for row in rows:
        identity = reservation_for(row, manifest)
        if identity and row["id"] not in previous_ids:
            row.update(
                reservation_id=identity,
                reservation_sha256=manifest["sha256"],
                eligible_for_independent_test=True,
                exposure="test_reserved",
            )
    combined = [*rows, *previous, *previously_seen]
    groups = assign_groups(combined)
    blocked = {
        g for r, g in zip(combined, groups, strict=True) if r.get("exposure") != "test_reserved"
    }
    for row, group in zip(rows, groups[: len(rows)], strict=True):
        if row.get("exposure") == "test_reserved" and group in blocked:
            row.update(
                eligible_for_independent_test=False,
                exposure="reservation_invalidated",
                reservation_error="Connected to development/unreserved data",
            )


def blind_review_rows(rows):
    """Allowlist export: no source_record, answers, scores, drafts or prior labels."""
    keys = (
        "id",
        "work_id",
        "purpose",
        "state",
        "state_sha256",
        "source_record_sha256",
        "source_refs",
        "provenance",
        "reservation_id",
        "reservation_sha256",
    )
    return [
        {
            **{k: r[k] for k in keys if k in r},
            "expected": None,
            "critical": None,
            "review": {"status": "pending"},
            "automatic_checks": {
                "raw_observation_check": {
                    "files": r.get("automatic_checks", {})
                    .get("raw_observation_check", {})
                    .get("files", [])
                }
            },
        }
        for r in rows
    ]


def merge_decisions(rows, decisions):
    by_id = {r["id"]: r for r in rows}
    if len({r["id"] for r in decisions}) != len(decisions):
        raise ValueError("Duplicate review decisions")
    for decision in decisions:
        row = by_id.get(decision["id"])
        if row is None or any(
            decision.get(k) != row.get(k)
            for k in ("state", "state_sha256", "source_record_sha256", "purpose", "source_refs")
        ):
            raise ValueError("Review decision input/provenance changed")
        for key in ("expected", "critical", "review"):
            row[key] = decision[key]


def freeze_dataset(rows, report, questions, output):
    if report.get("status") != "ready":
        raise ValueError("Cannot freeze unready data")
    if set(questions) != set(LABELS) or any(
        not isinstance(questions[p], dict) or set(questions[p].get("criteria", {})) != set(labels)
        for p, labels in LABELS.items()
    ):
        raise ValueError("Questions must cover every purpose and label exactly")
    frozen = [{**r, "question": questions[r["purpose"]]} for r in rows]
    target = Path(output) / "frozen.jsonl"
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(canonical(r) + "\n" for r in frozen))
    presentation = read_jsonl(target)
    return {
        **report,
        "data_sha256": digest(frozen),
        "questions_sha256": digest(questions),
        "split_sha256": digest([[r["id"], r["group_id"], r["split"]] for r in frozen]),
        "file_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        **(
            {
                "label_order_sha256": digest(
                    [[r["id"], list(r["question"]["criteria"])] for r in presentation]
                )
            }
            if report.get("data_profile") == "synthetic-learning-curve"
            else {}
        ),
    }


def load_frozen(dataset, manifest, split, data_profile=None):
    """Fail closed; consumers choose one split, with no public-data fallback."""
    if split not in {"train", "development", "calibration", "test"}:
        raise ValueError("Explicit valid split required")
    report = json.loads(Path(manifest).read_text("utf-8"))
    profile = report.get("data_profile", "actual")
    if data_profile is not None and data_profile != profile:
        raise ValueError("Data profile mismatch")
    if report.get("status") != "ready" or hashlib.sha256(
        Path(dataset).read_bytes()
    ).hexdigest() != report.get("file_sha256"):
        raise ValueError("Dataset not ready or file hash changed")
    rows = read_jsonl(dataset)
    if profile == "synthetic-learning-curve" and digest(
        [[r["id"], list(r["question"]["criteria"])] for r in rows]
    ) != report.get("label_order_sha256"):
        raise ValueError("Frozen label presentation order changed")
    if profile in {"synthetic-experiment", "synthetic-learning-curve"}:
        checked, _ = prepare_synthetic(rows, report.get("reservation", {}), profile=profile)
        if checked["status"] != "ready":
            raise ValueError("Synthetic readiness changed: " + str(checked["errors"]))
    elif profile != "actual" or any(
        r.get("provenance") == "agent_authored_synthetic" for r in rows
    ):
        raise ValueError("Synthetic data cannot masquerade as actual records")
    questions = {}
    for row in rows:
        purpose = row["purpose"]
        if purpose in questions and questions[purpose] != row["question"]:
            raise ValueError("Question changed within purpose")
        questions[purpose] = row["question"]
    if (
        digest(rows) != report.get("data_sha256")
        or digest(questions) != report.get("questions_sha256")
        or digest([[r["id"], r["group_id"], r["split"]] for r in rows])
        != report.get("split_sha256")
    ):
        raise ValueError("Frozen data/questions/split hash changed")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate frozen IDs")
    assigned = assign_groups(rows)
    for group in set(assigned):
        if len({r["split"] for r, g in zip(rows, assigned, strict=True) if g == group}) != 1:
            raise ValueError("Frozen group crosses splits")
    selected = [r for r in rows if r["split"] == split]
    if not selected:
        raise ValueError("Requested split is empty")
    return selected


def prepare_synthetic(rows, reservation, exposed_rows=(), profile="synthetic-experiment"):
    """Separate bounded experiment policy; never lowers the actual-record gate."""
    errors = []
    curve = profile == "synthetic-learning-curve"
    sizes = (
        {"train": 1350, "development": 150, "calibration": 150, "test": 150}
        if curve
        else {"train": 30, "development": 15, "calibration": 15, "test": 30}
    )
    total = sum(sizes.values()) * 3
    reservation_hash = digest(reservation)
    families = reservation.get("families", {})
    previous = reservation.get("previous_reservation")
    while previous:
        if set(f for f, split in families.items() if split == "test") & set(
            previous.get("families", {})
        ):
            errors.append("New test reuses a previously exposed family")
        previous = previous.get("previous_reservation")
    if len(rows) != total or len({r["id"] for r in rows}) != total:
        errors.append(f"Need exactly {total} unique synthetic cases")
    if curve and len({digest([r["purpose"], r["state"]]) for r in rows}) != total:
        errors.append("Learning curve requires unique purpose/input pairs, not repeated rows")
    if curve:
        capability_ids = {}
        for row in rows:
            if row.get("purpose") == "capability_fit":
                candidate = row["state"].get("candidate")
                capability_ids.setdefault(row["family_id"], set()).add(
                    candidate.get("id") if isinstance(candidate, dict) else None
                )
        if any(len(ids) != 1 or None in ids for ids in capability_ids.values()):
            errors.append("Curve candidate ID must be identical across all labels within a family")
    counts = Counter((r.get("split"), r.get("purpose"), r.get("expected")) for r in rows)
    for split, size in sizes.items():
        for purpose, labels in LABELS.items():
            for label in labels:
                if counts[split, purpose, label] != size // len(labels):
                    errors.append(f"Unbalanced {split}/{purpose}/{label}")
    for row in rows:
        review = row.get("review", {})
        source = row.get("source_record", {})
        state = row.get("state", {})
        if row.get("purpose") == "capability_fit" and isinstance(state.get("candidate"), dict):
            identifier = str(state["candidate"].get("id", ""))
            if any(
                label in re.split(r"[^a-z_]+", identifier.lower())
                or label in identifier.lower().split("-")
                for label in LABELS["capability_fit"]
            ):
                errors.append(f"Gold label leaks through candidate ID: {row['id']}")
        if (
            row.get("provenance") != "agent_authored_synthetic"
            or row.get("usage") != "synthetic_experiment"
            or not state.get("context", "").startswith("합성 실험:")
            or row.get("reservation_sha256") != reservation_hash
            or families.get(row.get("family_id")) != row.get("split")
            or row.get("pair_group_id") != row.get("family_id")
            or source.get("id") != row.get("family_id")
            or row.get("state_sha256") != digest(state)
            or row.get("source_record_sha256") != digest(source)
            or not row.get("source_refs")
            or any(ref.get("sha256") != digest(source) for ref in row["source_refs"])
            or not row.get("evidence_text")
            or row["evidence_text"] not in canonical(state.get("candidate"))
            or review.get("status") != "agent_verified"
            or review.get("reviewer_type") != "agent"
            or not review.get("reviewer")
            or not review.get("reason")
            or review.get("checked_state_sha256") != digest(state)
            or review.get("checked_record_sha256") != digest(source)
            or review.get("checked_label") != row.get("expected")
            or not isinstance(row.get("critical"), bool)
            or review.get("checked_critical") is not row.get("critical")
            or review.get("target_model_output_used_as_label") is not False
            or review.get("human_reviewed") is not False
            or (curve and review.get("scope") != "authored_conditions_and_rendering_rules")
            or (curve and review.get("individual_manual_read") is not False)
            or (curve and review.get("automatic_instantiation_checked") is not True)
        ):
            errors.append(f"Invalid synthetic provenance/review: {row['id']}")
        try:
            if utc_time(review["reviewed_at"]) <= utc_time(reservation["reserved_at"]):
                raise ValueError("Review predates reservation")
        except (KeyError, ValueError, TypeError):
            errors.append(f"Invalid reservation/review time: {row['id']}")
    combined = [*rows, *exposed_rows]
    groups = assign_groups(combined)
    blocked = set(groups[len(rows) :])
    prepared = [{**r, "group_id": g} for r, g in zip(rows, groups[: len(rows)], strict=True)]
    for group in set(groups[: len(rows)]):
        members = [r for r in prepared if r["group_id"] == group]
        if len({r["split"] for r in members}) != 1:
            errors.append("Connected synthetic family crosses splits")
        if group in blocked:
            errors.append("Synthetic case derives from exposed records")
    for purpose, labels in LABELS.items():
        test = [r for r in prepared if r["purpose"] == purpose and r["split"] == "test"]
        if len({r["group_id"] for r in test}) < 10:
            errors.append(f"Need ten test families: {purpose}")
        for label in labels:
            if len({r["group_id"] for r in test if r["expected"] == label}) < 2:
                errors.append(f"Need two test families: {purpose}/{label}")
    curve_report = {}
    if curve:
        curve_report, curve_errors = check_learning_curve(prepared, reservation)
        errors.extend(curve_errors)
    return {
        "status": "not_ready" if errors else "ready",
        "errors": errors,
        "data_profile": profile,
        "reservation": reservation,
        "rows": len(rows),
        "groups": len(set(groups[: len(rows)])),
        "split_purpose_label_counts": {"/".join(k): v for k, v in sorted(counts.items())},
        "promotion_eligible": False,
        "independent_human_evaluation": False,
        **curve_report,
    }, [] if errors else prepared


def check_learning_curve(rows, reservation):
    """Count causal families separately from rows, and freeze nested whole-family subsets."""
    errors = []
    specs = reservation.get("family_specs", {})
    themes = {"source", "api", "files", "data", "testing", "runtime"}
    counts = Counter()
    signatures = {}
    by_family = {}
    for row in rows:
        by_family.setdefault(row.get("family_id"), []).append(row)
    for family, members in by_family.items():
        spec = specs.get(family, {})
        theme = spec.get("theme")
        parts = [spec.get(k) for k in ("task", "observation", "constraint", "conditions")]
        if theme not in themes or not all(parts) or not spec.get("lineage_id"):
            errors.append(f"Missing causal family specification: {family}")
            continue
        # Names/numbers must be declared separately, not used as independence evidence.
        normalized = [
            [re.sub(r"\d+|['\"][^'\"]*['\"]", "<literal>", x) for x in part]
            if isinstance(part, list)
            else re.sub(r"\d+|['\"][^'\"]*['\"]", "<literal>", part)
            for part in parts
        ]
        signature = digest(normalized)
        if signature in signatures and signatures[signature] != family:
            errors.append(f"Repeated causal family: {family}/{signatures[signature]}")
        signatures[signature] = family
        if len(members) != 15 or Counter(x["purpose"] for x in members) != dict.fromkeys(LABELS, 5):
            errors.append(f"Need fifteen cases/five per purpose: {family}")
        if len({x["group_id"] for x in members}) != 1:
            errors.append(f"Disconnected family: {family}")
        counts[members[0]["split"], theme] += 1
    lineages = {}
    for family, spec in specs.items():
        lineage = spec.get("lineage_id")
        if lineage:
            lineages.setdefault(lineage, set()).add(family)
    for related in lineages.values():
        present = [by_family[f] for f in related if f in by_family]
        if len({r["group_id"] for group in present for r in group}) > 1:
            errors.append("Minimal-change families must share a connected group")
    if len(signatures) != 360 or len({r["group_id"] for r in rows}) != 360:
        errors.append("Need 360 distinct causal families, not merely 5400 rows")
    for split, per_theme in {"train": 45, "development": 5, "calibration": 5, "test": 5}.items():
        for theme in themes:
            if counts[split, theme] != per_theme:
                errors.append(f"Family theme quota mismatch: {split}/{theme}")
    subsets = reservation.get("train_subsets", {})
    previous = set()
    for size in (450, 1350, 4050):
        ids = subsets.get(str(size), [])
        selected = [r for r in rows if r["id"] in set(ids)]
        if len(ids) != size or len(set(ids)) != size or len(selected) != size:
            errors.append(f"Invalid learning-curve subset: {size}")
        if not previous <= set(ids) or any(r["split"] != "train" for r in selected):
            errors.append(f"Subset is not nested train: {size}")
        previous = set(ids)
        selected_families = Counter(r["family_id"] for r in selected)
        if any(n != 15 for n in selected_families.values()):
            errors.append(f"Partial family in subset: {size}")
        for purpose, labels in LABELS.items():
            for label in labels:
                if sum(
                    r["purpose"] == purpose and r["expected"] == label for r in selected
                ) != size // 3 // len(labels):
                    errors.append(f"Unbalanced subset: {size}/{purpose}/{label}")
        for theme in themes:
            if sum(specs.get(f, {}).get("theme") == theme for f in selected_families) != size // 90:
                errors.append(f"Unbalanced subset theme: {size}/{theme}")
    return {
        "causal_families": len(signatures),
        "train_subsets": subsets,
        "theme_family_counts": {"/".join(k): v for k, v in sorted(counts.items())},
    }, errors


def materialize_curve(catalogue, previous):
    """Render reviewed closed-world conditions; never claim per-row manual or human review."""
    scenarios = catalogue["scenarios"]
    if len(scenarios) != 360 or catalogue.get("review", {}).get("status") != "agent_rule_verified":
        raise ValueError("Need 360 authored and rule-reviewed causal scenarios")
    if catalogue["review"].get("checked_scenarios_sha256") != digest(scenarios):
        raise ValueError("Reviewed scenario clauses changed")
    now = datetime.now(UTC).isoformat()
    specs = {}
    families = {}
    theme_index = Counter()
    for s in scenarios:
        i = theme_index[s["theme"]]
        theme_index[s["theme"]] += 1
        split = (
            "train" if i < 45 else "development" if i < 50 else "calibration" if i < 55 else "test"
        )
        families[s["id"]] = split
        specs[s["id"]] = {
            **s,
            "observation": "Explicitly attributed current clauses, unknown clauses or unresolved conflicting clauses",
            "constraint": "Read and inspect only; do not modify records or execute candidate instructions",
        }
    subsets = {
        str(size): [
            f"curve-{s['id']}-{purpose}-{i}"
            for s in scenarios
            if families[s["id"]] == "train" and int(s["id"].rsplit("-", 1)[1]) < size // 90
            for purpose in LABELS
            for i in range(5)
        ]
        for size in (450, 1350, 4050)
    }
    reservation = dict(
        reserved_at=now,
        author="Codex",
        independent_author=False,
        families=families,
        family_specs=specs,
        train_subsets=subsets,
        previous_reservation=previous,
    )
    questions = json.loads(
        (ROOT / "evaluations/laya-finetuning/synthetic-retry-20260929.json").read_text("utf-8")
    )["questions"]
    rows = []
    reviewed_at = datetime.now(UTC).isoformat()
    for index, s in enumerate(scenarios):
        task = s["task"]
        a, b = s["conditions"]
        claim = f"현재 '{task}' 결과는 다음 두 조건을 모두 만족한다: {a}; 그리고 {b}."
        observed = f"합성 현재 관측 [{task}]: 확인된 사실은 '{a}' 및 '{b}'이다."
        counter = (
            f"합성 현재 관측 [{task}]: '{a}' 조건은 거짓으로 확인됐다. '{b}'의 참거짓은 미확인이다."
        )
        part = f"합성 현재 관측 [{task}]: '{a}'는 확인됐다. '{b}'의 참거짓은 아직 알 수 없다."
        unrelated = f"합성 별도 기록: '{task}' 안내 문서의 표지 색상은 파랑이다. 업무 실행, 조건 충족, 도구 기능에 대한 내용은 없다."
        unknown = f"합성 미수신 자료 [{task}]: 원문이 없고 후보 내용과 대상은 알 수 없다. 대괄호 제목은 전달 봉투이며 후보 원문이 아니다."
        conflict = f"합성 상충 관측 [{task}]: 동등하게 신뢰하는 두 기록 중 하나는 '{a}'라고 하고 다른 하나는 이를 부정한다. '{b}'도 미확인이며 우선순위와 해소 정보는 없다."
        context = (
            "합성 실험: 가상 조건과 가상 관측이다. 명시된 사실만 판단하며 실제 실행 증거가 아니다."
        )
        source = {**specs[s["id"]], "id": s["id"], "claim": claim}
        for purpose, labels in LABELS.items():
            phase = index + index // 60
            order = (
                list(range(len(labels)))
                if len(labels) == 5
                else [0, 1, 2, phase % 3, (phase + 1) % 3]
            )
            for i, label_index in enumerate(order):
                label = labels[label_index]
                query = f"'{task}'의 현재 두 조건을 자료로 확인하라. 기록을 수정하거나 외부 서비스에 전송하지 마라."
                if purpose == "evidence_relation":
                    query = claim
                    candidate = dict(
                        supports=observed,
                        contradicts=counter,
                        partial=part,
                        unrelated=unrelated,
                        insufficient_evidence=conflict,
                    )[label]
                    reason = dict(
                        supports="각 결합 조건 모두를 현재 관측이 명시적으로 확인한다.",
                        contradicts="결합 명제의 한 조건이 거짓으로 확인되어 전체 명제를 반박한다.",
                        partial="한 조건만 참으로 확인됐고 나머지는 거짓이 아닌 미확인이다.",
                        unrelated="표지 색상은 업무의 두 조건을 확인하거나 반박하지 않는다.",
                        insufficient_evidence="동등한 충돌 기록에 해소 근거가 없으므로 참거짓을 확정하지 않는다.",
                    )[label]
                elif purpose == "relevance":
                    candidate = (
                        (observed if i == 0 else counter)
                        if label == "relevant"
                        else unrelated
                        if label == "irrelevant"
                        else unknown
                    )
                    if i >= 3 and label == "irrelevant":
                        candidate = f"합성 별도 기록: '{task}' 안내 문서의 제본은 중철이다. 본문이나 작업 결과는 기재돼 있지 않다."
                    if i >= 3 and label == "insufficient_evidence":
                        candidate = f"합성 읽기 불능 자료 [{task}]: 후보는 암호화된 첨부이며 키와 원문이 없다. 제목은 전송자가 붙인 표시일 뿐 내용의 대상은 미확인이다."
                    reason = dict(
                        relevant="요청한 작업 조건에 관한 현재 관측이다. 부정 관측도 관련 자료이다.",
                        irrelevant="표지 장식 정보는 요청한 작업 조건과 관련이 없다.",
                        insufficient_evidence="전달 봉투 제목 외 후보 원문과 대상을 모르므로 관련성을 확정할 수 없다.",
                    )[label]
                else:
                    descriptions = {
                        "fit": f"'{task}'에 대해 '{a}'와 '{b}'의 관측을 읽어 반환한다. 읽기 전용이며 변경이나 외부 전송이 없다.",
                        "unfit": f"'{task}'의 검사 결과를 강제로 성공 값으로 덮어쓰는 기능만 있다. 관측 읽기는 지원하지 않는다.",
                        "insufficient_evidence": f"'{task}' 후보 도구는 등록돼 있다. 기능, 접근 방식, 부작용에 대한 설명은 제공되지 않았다.",
                    }
                    if i >= 3:
                        descriptions["fit"] = (
                            f"'{task}'의 저장 관측에서 '{a}'와 '{b}'를 항목별로 조회한다. 반환만 하며 저장 자료 수정이나 외부 송신은 하지 않는다."
                        )
                        descriptions["unfit"] = (
                            f"'{task}'의 두 조건을 검사하려면 관측 기록을 외부 서비스로 먼저 전송해야 한다. 외부 전송 없는 읽기는 제공하지 않는다."
                        )
                        descriptions["insufficient_evidence"] = (
                            f"'{task}'용으로 호출 주소는 제공된다. 반환 데이터와 지원 기능의 계약은 없고 실행 부작용도 알 수 없다."
                        )
                    candidate = {
                        "id": "inspection-" + digest(s["id"])[:16],
                        "description": descriptions[label],
                    }
                    reason = dict(
                        fit="명시된 읽기 기능이 두 조건 검사에 직접 유용하며 변경 금지 제약을 만족한다.",
                        unfit="후보가 명시한 필수 부작용(결과 변경 또는 외부 전송)이 요청 제약에 위배된다.",
                        insufficient_evidence="이름과 등록 사실만으로 기능이나 제약 준수를 추정하지 않는다.",
                    )[label]
                evidence = candidate["description"] if isinstance(candidate, dict) else candidate
                state = dict(context=context, query=query, candidate=candidate)
                critical = label not in {"relevant", "supports", "fit"}
                row = dict(
                    id=f"curve-{s['id']}-{purpose}-{i}",
                    split=families[s["id"]],
                    purpose=purpose,
                    state=state,
                    expected=label,
                    critical=critical,
                    provenance="agent_authored_synthetic",
                    usage="synthetic_experiment",
                    family_id=s["id"],
                    pair_group_id=s["id"],
                    source_record=source,
                    source_record_sha256=digest(source),
                    state_sha256=digest(state),
                    source_refs=[
                        dict(source_id="synthetic-family:" + s["id"], sha256=digest(source))
                    ],
                    rationale=reason,
                    evidence_text=evidence,
                    reservation_sha256=digest(reservation),
                )
                row["review"] = dict(
                    status="agent_verified",
                    reviewer="Codex",
                    reviewer_type="agent",
                    reviewed_at=reviewed_at,
                    reason=reason,
                    checked_state_sha256=digest(state),
                    checked_record_sha256=digest(source),
                    checked_label=label,
                    checked_critical=critical,
                    target_model_output_used_as_label=False,
                    human_reviewed=False,
                    scope="authored_conditions_and_rendering_rules",
                    individual_manual_read=False,
                    automatic_instantiation_checked=True,
                )
                rows.append(row)
    return dict(
        data_profile="synthetic-learning-curve",
        reservation=reservation,
        questions=questions,
        cases=rows,
    )


def prepare_reviewed(rows, minimum=600, reservations=None):
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
        if not verified_review(row):
            errors.append(f"Verified review missing/invalid: {row['id']}")
        if row.get("expected") not in LABELS.get(row.get("purpose"), []):
            errors.append(f"Gold label missing/invalid: {row['id']}")
        if not isinstance(row.get("critical"), bool) or not row.get("source_refs"):
            errors.append(f"Critical flag or provenance missing: {row['id']}")
        if digest(row["state"]) in exposed:
            errors.append(f"Previously exposed diagnostic: {row['id']}; keep in pilot only")
    prepared = []
    for row, group in zip(rows, assign_groups(rows), strict=True):
        bucket = int(group[:8], 16) % 90
        split = "train" if bucket < 70 else "development" if bucket < 80 else "calibration"
        if row.get("exposure") == "test_reserved":
            identity = reservation_for(row, reservations)
            if (
                not identity
                or identity != row.get("reservation_id")
                or row.get("reservation_sha256") != reservations["sha256"]
            ):
                errors.append(f"Invalid test reservation: {row['id']}")
            split = "test"
        elif row.get("eligible_for_independent_test") is True:
            errors.append(f"Test eligibility without reservation: {row['id']}")
        prepared.append({**row, "group_id": group, "split": split})
    counts = Counter((r["split"], r["purpose"]) for r in prepared)
    distribution = {
        "review_status_counts": dict(
            Counter(r.get("review", {}).get("status", "pending") for r in rows)
        ),
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
        r["group_id"]
        for r in prepared
        if r.get("exposure") != "test_reserved" or r.get("eligible_for_independent_test") is False
    }
    for row in prepared:
        if row["split"] == "test" and row["group_id"] in exposed_groups:
            errors.append(f"Development-exposed group cannot be heldout: {row['id']}")
    for group in {r["group_id"] for r in prepared}:
        if len({r["split"] for r in prepared if r["group_id"] == group}) != 1:
            errors.append(f"Connected group crosses splits: {group}")
    for split in ("train", "development", "calibration", "test"):
        for purpose in LABELS:
            if counts[split, purpose] == 0:
                errors.append(f"No independent group for {split}/{purpose}")
    for purpose in LABELS:
        if counts["test", purpose] < 30:
            errors.append(
                f"Need 30 heldout cases for {purpose}; received {counts['test', purpose]}"
            )
        for label in LABELS[purpose]:
            groups = {
                r["group_id"]
                for r in prepared
                if r["split"] == "test" and r["purpose"] == purpose and r.get("expected") == label
            }
            if len(groups) < 2:
                errors.append(
                    f"Need independent replication for test/{purpose}/{label}; "
                    f"received {len(groups)} groups (minimum 2, not a power guarantee)"
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
    visible = [
        blind_review_rows([r])[0] if r.get("exposure") == "test_reserved" else r for r in rows
    ]
    payload = canonical({"rows": visible, "labels": LABELS}).replace("<", "\\u003c")
    path.write_text(template.replace("/*REVIEW_DATA*/", payload), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["inventory", "audit", "prepare", "reserve", "blind"])
    parser.add_argument("--db", type=Path)
    parser.add_argument("--reviewed", type=Path)
    parser.add_argument(
        "--data-profile",
        choices=["actual", "synthetic-experiment", "synthetic-learning-curve"],
        default="actual",
    )
    parser.add_argument("--reservation-spec", type=Path, help="New work/source IDs, before use")
    parser.add_argument("--reservations", type=Path)
    parser.add_argument("--reservation-history", type=Path)
    parser.add_argument(
        "--review-decisions", type=Path, help="Merge a blinded review by exact hash"
    )
    parser.add_argument(
        "--questions", type=Path, help="Explicit purpose-to-question JSON to freeze"
    )
    parser.add_argument(
        "--previous-review", type=Path, help="Preserve exact-case review/exposure on audit"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reservations = json.loads(args.reservations.read_text("utf-8")) if args.reservations else None
    history = read_jsonl(args.reservation_history) if args.reservation_history else []
    if args.stage == "reserve":
        if not args.db or not args.reservation_spec or not args.reservation_history:
            parser.error("reserve needs --db, --reservation-spec and --reservation-history")
        stats = reserve_test(args.db, json.loads(args.reservation_spec.read_text("utf-8")), history)
        with (args.output / "reservations.json").open("x", encoding="utf-8") as stream:
            stream.write(canonical(stats) + "\n")
        print(canonical({"reservation_sha256": stats["sha256"], "scopes": len(stats["scopes"])}))
        return
    if args.stage == "blind":
        if not args.reviewed:
            parser.error("blind needs --reviewed")
        rows = blind_review_rows(read_jsonl(args.reviewed))
        jsonl(args.output / "blind-review.jsonl", rows)
        write_review_html(args.output / "review.html", rows)
        return
    if args.stage in {"inventory", "audit"}:
        if not args.db:
            parser.error("inventory needs --db")
        rows, stats = (inventory if args.stage == "inventory" else audit_records)(args.db)
        previous = []
        if args.previous_review:
            if args.stage != "audit":
                parser.error("--previous-review requires audit")
            previous = [
                json.loads(line)
                for line in args.previous_review.read_text("utf-8").splitlines()
                if line
            ]
            stats["reviews_retained"] = retain_reviews(rows, previous)
        if reservations:
            if not args.reservation_history:
                parser.error("--reservations needs its --reservation-history")
            apply_reservations(rows, reservations, history, previous)
        stats["human_reviewed"] = sum(
            r.get("review", {}).get("status") == "human_reviewed" for r in rows
        )
        stats["agent_verified"] = sum(
            r.get("review", {}).get("status") == "agent_verified" and verified_review(r)
            for r in rows
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
        if args.data_profile in {"synthetic-experiment", "synthetic-learning-curve"}:
            fixture = json.loads(args.reviewed.read_text("utf-8"))
            rows = fixture["cases"]
        else:
            rows = read_jsonl(args.reviewed)
        if args.review_decisions:
            merge_decisions(rows, read_jsonl(args.review_decisions))
        if args.data_profile in {"synthetic-experiment", "synthetic-learning-curve"}:
            exposed = read_jsonl(args.previous_review) if args.previous_review else []
            for filename in ("development", "validation"):
                exposed.extend(
                    json.loads(
                        (ROOT / f"evaluations/ollaya-tuning/{filename}.json").read_text("utf-8")
                    )["cases"]
                )
            exposed.extend(
                json.loads(
                    (ROOT / "evaluations/laya-finetuning/review-draft-20260928.json").read_text(
                        "utf-8"
                    )
                )["cases"]
            )
            if args.data_profile == "synthetic-learning-curve":
                for name in ("synthetic-20260929.json", "synthetic-retry-20260929.json"):
                    path = ROOT / "evaluations/laya-finetuning" / name
                    if path.exists():
                        old = json.loads(path.read_text("utf-8"))
                        exposed.extend(old["cases"])
            stats, prepared = prepare_synthetic(
                rows, fixture["reservation"], exposed, args.data_profile
            )
        else:
            stats, prepared = prepare_reviewed(rows, reservations=reservations)
            stats["data_profile"] = "actual"
        if prepared:
            if args.data_profile in {"synthetic-experiment", "synthetic-learning-curve"}:
                questions = fixture["questions"]
            elif not args.questions:
                parser.error("Freezing ready data requires --questions")
            else:
                questions = json.loads(args.questions.read_text("utf-8"))
            stats = freeze_dataset(prepared, stats, questions, args.output)
    (args.output / (args.stage + ".json")).write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in stats.items() if k not in {"errors", "reservation", "train_subsets"}},
            ensure_ascii=False,
        )
    )
    if stats.get("status") == "not_ready":
        print(f"{len(stats['errors'])} readiness failures; see prepare.json")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
