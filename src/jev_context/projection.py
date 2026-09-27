"""Small read projections; persisted events and evidence remain unchanged."""


def checkpoint(work):
    if work.get("status") == "completion_reported":
        return {
            "status": work["status"],
            "summary": work.get("completion_summary", ""),
            "next_actions": [],
            "open_items": work.get("open_items", []),
            "completion_coverage": work.get("completion_coverage", "incomplete"),
        }
    return {"status": work.get("status", "active"), **work.get("progress", {})}


def criterion_summary(criterion):
    if criterion.get("status") != "not_applicable":
        return dict(criterion)
    return {
        key: criterion[key]
        for key in ("criterion_id", "status", "reason", "target_revision", "provenance")
        if key in criterion
    }


def observation_summary(item):
    result = {
        key: item[key]
        for key in ("id", "claim", "role", "target_revision", "provenance", "source_refs")
        if key in item
    }
    # Keep the reported failure/counterclaim intact. Raw logs stay available in evidence view.
    result["observation"] = {
        key: item["observation"][key]
        for key in ("command", "exit_code", "target_hash", "outcome")
        if key in item.get("observation", {})
    }
    result["details_omitted"] = bool(set(item.get("observation", {})) - set(result["observation"]))
    return result


def work_summary(work):
    if work.get("redacted"):
        return work
    result = dict(work)
    current = checkpoint(work)
    result["checkpoint"] = current
    if work.get("status") == "completion_reported":
        result["progress"] = {"summary": current["summary"], "next_actions": []}
    if "criteria" in work:
        result["criteria"] = {k: criterion_summary(c) for k, c in work["criteria"].items()}
    result["inspection"] = {
        "work_id": work["work_id"],
        "views": ["events", "criteria", "evidence"],
    }
    return result
