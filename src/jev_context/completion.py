from .common import DomainError


def record_criterion(service, work, event):
    criteria = work.setdefault("criteria", {})
    cid = event["criterion_id"]
    if event["kind"] == "criterion_registered":
        if cid in criteria:
            raise DomainError("invalid_argument", "Criterion ID already registered")
        criteria[cid] = {**event, "status": "not_run", "provenance": "agent_reported"}
        work["completion_coverage"] = "incomplete"
        return []
    if cid not in criteria:
        raise DomainError("not_found", "Register the criterion before reporting its result")
    criterion = criteria[cid]
    if criterion["target_revision"] != event["target_revision"]:
        raise DomainError(
            "revision_conflict", "Criterion targets another revision; register a new criterion"
        )
    evidence, refs = [], []
    for eid in event["evidence_ids"]:
        item = service.allowed_body(
            "evidence", eid, service.store.body("evidence", eid, work["work_id"])
        )
        if item.get("redacted") or item["target_revision"] != event["target_revision"]:
            raise DomainError("input_incomplete", "Evidence is missing or targets another revision")
        evidence.append(item)
        refs += service.store.inherited_refs("evidence", eid)
    if event["status"] == "passed" and (
        not evidence
        or any(
            e["observation"].get("exit_code") != 0 or not e["observation"].get("target_hash")
            for e in evidence
        )
    ):
        raise DomainError(
            "input_incomplete", "Passing requires successful revision-bound check evidence"
        )
    criterion.update({k: event[k] for k in ("status", "target_revision", "evidence_ids", "reason")})
    work["completion_coverage"] = "incomplete"
    return refs


def coverage(service, work, target):
    required = [c for c in work.get("criteria", {}).values() if c["required"]]
    if (
        not required
        or work.get("open_items")
        or any(i.get("status") == "open" for i in service.objects("issues", work["work_id"]))
    ):
        return "incomplete"
    for criterion in required:
        if criterion["status"] == "not_applicable":
            if not criterion.get("reason"):
                return "incomplete"
            continue
        if criterion["target_revision"] != target or criterion["status"] != "passed":
            return "incomplete"
        for eid in criterion.get("evidence_ids", []):
            item = service.allowed_body(
                "evidence", eid, service.store.body("evidence", eid, work["work_id"])
            )
            if item.get("redacted") or item.get("target_revision") != target:
                return "incomplete"
    return "reported_complete"
