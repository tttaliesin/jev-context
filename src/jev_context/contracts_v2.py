"""Explicit opt-in v2 schemas, extending the frozen v1 wire contract."""

from copy import deepcopy


def contract_v2(v1):
    contract = deepcopy(v1)
    contract["contract_version"] = "2.0"
    contract["output"]["properties"]["contract_version"] = {"const": "2.0"}
    tools = contract["tools"]
    identifier = tools["workspace_status"]["properties"]["request_id"]
    text = {"type": "string", "minLength": 1, "maxLength": 2000}

    def array(item, count=32):
        return {"type": "array", "items": item, "maxItems": count}

    def obj(properties, required=None):
        return {
            "type": "object",
            "properties": properties,
            "required": required if required is not None else list(properties),
            "additionalProperties": False,
        }

    events = tools["work_record"]["properties"]["event"]["oneOf"]
    refs = events[0]["properties"]["source_refs"]
    events.extend(
        [
            obj(
                {
                    "kind": {"const": "criterion_registered"},
                    "criterion_id": identifier,
                    "description": text,
                    "target_revision": text,
                    "required": {"type": "boolean"},
                    "source_refs": refs,
                }
            ),
            obj(
                {
                    "kind": {"const": "criterion_result"},
                    "criterion_id": identifier,
                    "status": {
                        "enum": ["passed", "failed", "not_run", "unknown", "not_applicable"]
                    },
                    "target_revision": text,
                    "evidence_ids": array(identifier),
                    "reason": text,
                }
            ),
        ]
    )
    for event in events:
        if event["properties"]["kind"]["const"] == "evidence_reported":
            event["properties"]["role"] = {"enum": ["support", "counterevidence", "failure"]}
        if event["properties"]["kind"]["const"] == "completion_reported":
            event["properties"]["target_revision"] = text
            event["required"].append("target_revision")
    tools["work_inspect"]["properties"]["view"]["enum"].extend(["criteria", "judgments"])
    tools["work_inspect"]["properties"]["packet_id"] = identifier
    tools["context_prepare"]["properties"]["budget_bytes"]["minimum"] = 1024
    tools["context_prepare"]["properties"]["judge_mode"]["enum"].append("required")
    tools["context_prepare"]["properties"]["claim"] = text
    tools["context_prepare"]["properties"]["language"] = {"enum": ["ko", "en"], "default": "ko"}
    inventory = obj(
        {
            "revision": identifier,
            "observed_at": {"type": "string", "maxLength": 40},
            "complete": {"type": "boolean"},
            "items": array(
                obj(
                    {
                        "id": identifier,
                        "kind": {"enum": ["tool", "skill", "worker"]},
                        "description": text,
                        "version": text,
                        "available": {"type": "boolean"},
                        "mandatory": {"type": "boolean"},
                    }
                ),
                32,
            ),
        }
    )
    tools["capability_recommend"] = obj(
        {
            "request_id": identifier,
            "work_id": identifier,
            "query": text,
            "inventory": inventory,
            "expected_inventory_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "required_ids": array(identifier),
            "language": {"enum": ["ko", "en"]},
            "budget_bytes": {"type": "integer", "minimum": 1024, "maximum": 65536},
        },
        ["request_id", "work_id", "query", "inventory"],
    )
    tools["handoff_prepare"] = obj(
        {
            "request_id": identifier,
            "work_id": identifier,
            "expected_work_revision": {"type": "integer", "minimum": 1},
            "query": text,
            "role": text,
            "round": {"type": "integer", "minimum": 0},
            "mode": {"enum": ["read", "write"]},
            "required_refs": refs,
            "budget_bytes": {"type": "integer", "minimum": 1024, "maximum": 65536},
        },
        ["request_id", "work_id", "expected_work_revision", "query", "role", "round", "mode"],
    )
    for schema in tools.values():
        for branch in schema.get("oneOf", [schema]):
            branch["properties"]["contract_version"] = {"const": "2.0"}
            branch["required"].append("contract_version")
    return contract
