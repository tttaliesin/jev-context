from .common import dumps, response


def measure(result):
    return len(
        dumps(
            {
                "content": [{"type": "text", "text": dumps(result)}],
                "structuredContent": result,
                "isError": result["outcome"] in {"error", "conflict"},
            }
        ).encode("utf-8")
    )


def bounded(result, limit, removable_key=None):
    """Bound the entire v2 service envelope, including evidence metadata and errors."""
    result["contract_version"] = "2.0"
    data = result["data"]
    data["wire_budget"] = {
        "limit_bytes": limit,
        "used_bytes": 0,
        "measurement": "UTF-8 MCP CallToolResult JSON; excludes JSON-RPC framing",
    }
    for _ in range(8):
        data["wire_budget"]["used_bytes"] = measure(result)
    if measure(result) <= limit:
        return result
    judgment = data.get("judgment", {})
    if judgment.get("inspection") and judgment.get("evaluations"):
        judgment["evaluation_count"] = len(judgment.pop("evaluations"))
    if removable_key:
        while data.get(removable_key) and measure(result) > limit:
            removable = [
                i
                for i, item in enumerate(data[removable_key])
                if item.get("role") != "required_evidence" and not item.get("mandatory")
            ]
            if not removable:
                break
            data[removable_key].pop(removable[-1])
            data["wire_budget"]["omitted_optional"] = (
                data["wire_budget"].get("omitted_optional", 0) + 1
            )
            data["wire_budget"]["used_bytes"] = measure(result)
    for _ in range(8):
        data["wire_budget"]["used_bytes"] = measure(result)
    if measure(result) <= limit:
        return result
    # Large judgment traces are inspection metadata; never return an oversized packet.
    minimum = measure(result)
    reduced = response(
        result["request_id"],
        {
            "reason": "budget_exceeded",
            "minimum_required_bytes": minimum,
            "packet_id": data.get("packet_id"),
        },
        "insufficient",
    )
    reduced["contract_version"] = "2.0"
    reduced["data"]["wire_budget"] = {"limit_bytes": limit, "used_bytes": 0}
    for _ in range(8):
        reduced["data"]["wire_budget"]["used_bytes"] = measure(reduced)
    return reduced
