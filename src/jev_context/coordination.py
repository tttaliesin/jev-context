import json
import time
from datetime import UTC, datetime

from .budget import bounded
from .common import DomainError, digest, dumps, response, uid
from .judgment import evaluate_many, usable


def inventory_hash(inventory):
    return digest(dumps(inventory).encode())


def validate_inventory(inventory, expected=None):
    try:
        observed = datetime.fromisoformat(inventory["observed_at"].replace("Z", "+00:00"))
        age = (datetime.now(UTC) - observed).total_seconds()
        if not 0 <= age <= 60:
            raise ValueError()
    except (ValueError, TypeError):
        raise DomainError(
            "input_incomplete", "Inventory needs a fresh timezone-aware observation"
        ) from None
    ids = [item["id"] for item in inventory["items"]]
    if len(ids) != len(set(ids)):
        raise DomainError("invalid_argument", "Duplicate capability IDs")
    fingerprint = inventory_hash(inventory)
    if expected and expected != fingerprint:
        raise DomainError("revision_conflict", "Capability inventory changed; recommend again")
    return fingerprint


def capability_recommend(service, args):
    work = service.work(args["work_id"])
    if work.get("redacted"):
        raise DomainError("input_incomplete", "Work origin is unavailable")
    inventory = args["inventory"]
    fingerprint = validate_inventory(inventory, args.get("expected_inventory_hash"))
    required = set(args.get("required_ids", [])) | {
        i["id"] for i in inventory["items"] if i["mandatory"]
    }
    available = {i["id"] for i in inventory["items"] if i["available"]}
    missing = sorted(required - available)
    selected, observations = [], []
    deadline = time.monotonic() + getattr(service.config, "judgment_seconds", 2.0)
    language = args.get("language", "ko")
    items = [i for i in inventory["items"] if i["available"]]
    entries = [
        (
            {
                "query": args["query"],
                "goal": work["goal"],
                "scope": work["scope"],
                "candidate": item,
            },
            ["capability_fit"],
        )
        for item in items[:8]
    ]
    results = evaluate_many(service, entries, deadline, language, args["work_id"])
    results.extend({"status": "abstained", "reason": "candidate_limit"} for _ in items[8:])
    for item, result in zip(items, results, strict=True):
        observations.append({"id": item["id"], **result})
        answer = next(iter(result.get("answers", [])), None)
        is_required = item["id"] in required
        if is_required or (
            answer
            and answer["choice"] == "fit"
            and usable(service, answer, "capability_fit", language)
        ):
            selected.append(
                {
                    **item,
                    "mandatory": is_required,
                    "selection_reason": "required" if is_required else "evaluated_model",
                }
            )
    validate_inventory(inventory, fingerprint)
    if service.work(args["work_id"])["revision"] != work["revision"]:
        raise DomainError("revision_conflict", "Work changed during recommendation")
    observed_all = all(o["status"] == "observed" for o in observations)
    from .judgment import promotion

    active = promotion(service, "capability_fit", language)[0]
    packet_id = None
    if any("request" in item for item in observations) and not service.config.read_only:
        packet_id = uid("packet")
        body = {
            "packet_id": packet_id,
            "kind": "capability_recommend",
            "scope": {"goal": work["goal"], "constraints": work["scope"]["constraints"]},
            "work_revision": work["revision"],
            "inventory_hash": fingerprint,
            "inventory_revision": inventory["revision"],
            "judgment": {"evaluations": observations},
        }
        with service.store.transaction():
            if service.work(args["work_id"])["revision"] != work["revision"]:
                raise DomainError("revision_conflict", "Work changed before recording judgment")
            service.store.check_space(len(dumps(body).encode()) + 4096)
            service.db.execute(
                "INSERT INTO packets VALUES(?,?,?,?)",
                (packet_id, args["work_id"], dumps(body), "valid_at_read"),
            )
    return bounded(
        response(
            args["request_id"],
            {
                "work_revision": work["revision"],
                "inventory_hash": fingerprint,
                "inventory_revision": inventory["revision"],
                "inventory_provenance": "agent_reported",
                "selected": selected,
                "evaluations": [
                    {key: value for key, value in item.items() if key != "request"}
                    for item in observations
                ],
                "inspection": {"view": "judgments", "packet_id": packet_id},
                "missing_required": missing,
                "inventory_complete": inventory["complete"],
                "model_mode": "active" if active else "shadow",
                "use_condition": "Recheck availability and version against current host inventory before use",
                "execution_authorized": False,
            },
            "insufficient"
            if missing
            else "ok"
            if observed_all and inventory["complete"] and active
            else "partial",
        ),
        args.get("budget_bytes", 16384),
        "selected",
    )


def handoff_prepare(service, args):
    if args["mode"] == "write":
        raise DomainError(
            "unsupported", "Write handoff requires a verified host isolation/ownership adapter"
        )
    work = service.work(args["work_id"])
    if work["revision"] != args["expected_work_revision"]:
        raise DomainError("revision_conflict", "Work changed before handoff")
    budget = args.get("budget_bytes", 16384)
    context_args = {
        k: args[k] for k in ("request_id", "work_id", "query", "expected_work_revision")
    }
    context_args.update(
        contract_version="2.0",
        required_refs=args.get("required_refs", []),
        budget_bytes=max(1024, budget - 768),
    )
    packet = service.context_prepare(context_args)
    if packet["outcome"] in {"error", "conflict", "insufficient"}:
        return bounded(packet, budget)
    body = packet["data"]
    identity = {
        "project_id": service.config.project_id,
        "work_id": args["work_id"],
        "work_revision": body["work_revision"],
        "goal": work["goal"],
        "scope": work["scope"],
        "source_set_revision": body["source_set_revision"],
        "role": args["role"],
        "query": args["query"],
        "round": args["round"],
        "required_refs": args.get("required_refs", []),
    }
    hid = "handoff-" + digest(dumps(identity).encode())
    body.update(
        handoff_id=hid,
        role=args["role"],
        round=args["round"],
        mode="read",
        independent_search_allowed=True,
        execution_status="not_dispatched",
    )
    result = bounded(packet, budget)
    if result["outcome"] == "insufficient" or service.config.read_only:
        return result
    with service.store.transaction():
        if (
            service.work(args["work_id"])["revision"] != work["revision"]
            or service.store.meta()["source_set_revision"] != body["source_set_revision"]
        ):
            raise DomainError("revision_conflict", "Handoff snapshot changed")
        existing = service.db.execute(
            "SELECT body,status FROM packets WHERE id=?", (hid,)
        ).fetchone()
        if existing and existing["status"] == "valid_at_read":
            return bounded(
                response(args["request_id"], json.loads(existing["body"]), packet["outcome"]),
                budget,
            )
        service.store.check_space(len(dumps(result["data"]).encode()) + 4096)
        service.db.execute(
            "INSERT OR REPLACE INTO packets VALUES(?,?,?,?)",
            (hid, args["work_id"], dumps(result["data"]), "valid_at_read"),
        )
        service.store.refs(
            "packets",
            hid,
            [
                {k: e[k] for k in ("source_id", "revision", "start_line", "end_line")}
                for e in body.get("evidence", [])
            ],
        )
    return result
