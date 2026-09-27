from __future__ import annotations

import base64
import contextlib
import json
import sqlite3
import time
from importlib.resources import files

from jsonschema import Draft202012Validator

from .common import DomainError, digest, dumps, failed, now, response, uid
from .contracts_v2 import contract_v2
from .sources import Sources, stale_file_sources
from .storage import FileLock, Store

CONTRACT = json.loads(files("jev_context").joinpath("contracts.json").read_text(encoding="utf-8"))
VALIDATORS = {name: Draft202012Validator(schema) for name, schema in CONTRACT["tools"].items()}
CONTRACT_V2 = contract_v2(CONTRACT)
VALIDATORS_V2 = {
    name: Draft202012Validator(schema) for name, schema in CONTRACT_V2["tools"].items()
}
MUTATING = {"work_record", "source_sync", "data_forget"}
RECORD_HANDLERS = {
    "decision_proposed": "_record_decision_proposed",
    "decision_adopted": "_record_decision_transition",
    "decision_superseded": "_record_decision_transition",
    "scope_revised": "_record_scope_revised",
    "issue_opened": "_record_issue_opened",
    "issue_resolved": "_record_issue_resolved",
    "evidence_reported": "_record_evidence_reported",
    "criterion_registered": "_record_criterion",
    "criterion_result": "_record_criterion",
    "progress_reported": "_record_progress_reported",
    "completion_reported": "_record_completion_reported",
    "work_reopened": "_record_work_reopened",
}


class _Record:
    """One work_record event: its new IDs, accumulated source refs and response."""

    def __init__(self, service, wid, body, event):
        self.service, self.wid, self.body, self.event = service, wid, body, event
        self.eid = uid("event")
        self.refs = event.get("source_refs", []) + event.get("origin", {}).get("source_refs", [])
        self.result = dict(work_id=wid, event_id=self.eid, revision=body["revision"] + 1)

    def insert(self, table, prefix, payload):
        object_id = uid(prefix)
        payload.update(id=object_id, provenance="agent_reported", event_id=self.eid)
        self.service.db.execute(
            f"INSERT INTO {table} VALUES(?,?,?)", (object_id, self.wid, dumps(payload))
        )
        self.service.store.refs(table, object_id, self.refs)
        self.result[prefix + "_id"] = object_id
        return object_id


class Service:
    def __init__(self, config, engine=None):
        self.config = config
        self.engine = engine
        self.store = Store(config)
        self.db = self.store.db
        self.sources = Sources(self.store)

    def close(self):
        self.store.close()

    def call(self, tool, args):
        result = self._call(tool, args)
        result["contract_version"] = self.config.contract_version
        return result

    def _call(self, tool, args):
        request_id = args.get("request_id", "invalid") if isinstance(args, dict) else "invalid"
        try:
            validators = VALIDATORS_V2 if self.config.contract_version == "2.0" else VALIDATORS
            if tool not in validators:
                raise DomainError("invalid_argument", "Unknown tool")
            errors = list(validators[tool].iter_errors(args))
            if errors:
                raise DomainError(
                    "invalid_argument",
                    "Input does not match tool schema: "
                    + "/".join(map(str, errors[0].absolute_path)),
                )
            if len(dumps(args).encode()) > 512 * 1024:
                raise DomainError("invalid_argument", "Request exceeds size limit")
            self.validate_bytes(args)
            if self.store.meta()["policy_hash"] != self.config.policy_hash:
                raise DomainError(
                    "policy_denied", "Installation policy changed; reload this server"
                )
            writing = tool in MUTATING or (tool == "work_open" and "create" in args)
            if writing and self.config.read_only:
                raise DomainError("policy_denied", "Installation is read-only")
            if tool == "source_sync":
                return self.sync(args)
            if writing:
                guard = (
                    FileLock(self.config.db_path.parent / "ingestion.lock")
                    if tool == "data_forget"
                    else contextlib.nullcontext()
                )
                with guard, self.store.transaction():
                    replay = self.mutation_start(tool, args)
                    if replay is not None:
                        return replay
                    if tool != "data_forget":
                        self.store.check_space(len(dumps(args).encode()) * 4 + 4096)
                    data = getattr(self, tool)(args)
                    result = response(request_id, data)
                    self.mutation_finish(tool, args, result)
                    return result
            if tool == "context_prepare":
                return self.context_prepare(args)
            if tool in {"capability_recommend", "handoff_prepare"}:
                from .coordination import capability_recommend, handoff_prepare

                return {
                    "capability_recommend": capability_recommend,
                    "handoff_prepare": handoff_prepare,
                }[tool](self, args)
            # One read transaction keeps projections and revision-bound pagination consistent.
            self.db.execute("BEGIN")
            try:
                return response(request_id, getattr(self, tool)(args))
            finally:
                self.db.execute("COMMIT")
        except DomainError as exc:
            return failed(request_id, exc)
        except (UnicodeError, RecursionError):
            return failed(
                request_id, DomainError("invalid_argument", "Invalid Unicode or excessive nesting")
            )
        except sqlite3.Error as exc:
            busy = "locked" in str(exc).lower() or "busy" in str(exc).lower()
            return failed(
                request_id,
                DomainError(
                    "busy" if busy else "storage_failed",
                    "Database is busy" if busy else "Database operation failed",
                    busy,
                ),
            )

    def validate_bytes(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    key in {"quote", "query", "text", "result_excerpt"}
                    and isinstance(item, str)
                    and len(item.encode()) > 8192
                ):
                    raise DomainError("invalid_argument", f"{key} exceeds UTF-8 byte limit")
                self.validate_bytes(item)
        elif isinstance(value, list):
            for item in value:
                self.validate_bytes(item)

    def mutation_start(self, tool, args):
        semantic = json.loads(dumps({k: v for k, v in args.items() if k != "request_id"}))
        if tool == "work_open" and "create" in semantic:
            semantic["create"]["scope"] = self.scope_defaults(semantic["create"]["scope"])
        if tool == "work_record" and semantic["event"]["kind"] == "scope_revised":
            semantic["event"]["scope"] = self.scope_defaults(semantic["event"]["scope"])
        input_hash = digest(dumps(semantic).encode())
        row = self.db.execute(
            "SELECT * FROM mutations WHERE tool=? AND id=?", (tool, args["mutation_id"])
        ).fetchone()
        if row:
            if row["input_hash"] != input_hash:
                raise DomainError(
                    "idempotency_mismatch", "Mutation ID was used with different input"
                )
            if row["result"]:
                result = json.loads(row["result"])
                result["request_id"] = args["request_id"]
                return result
            if tool != "source_sync":
                raise DomainError("busy", "Mutation is in progress", True)
        else:
            self.db.execute(
                "INSERT INTO mutations VALUES(?,?,?,?,?,?,?)",
                (
                    tool,
                    args["mutation_id"],
                    input_hash,
                    args.get("work_id"),
                    "processing",
                    None,
                    now(),
                ),
            )
        return None

    def mutation_finish(self, tool, args, result):
        self.db.execute(
            "UPDATE mutations SET state='complete',result=?,work_id=COALESCE(work_id,?) WHERE tool=? AND id=?",
            (dumps(result), result["data"].get("work_id"), tool, args["mutation_id"]),
        )

    def scope_defaults(self, scope):
        scope = dict(scope)
        scope.setdefault(
            "allowed_actions", ["read", "write_design"] if scope["mode"] == "design" else ["read"]
        )
        return scope

    def allowed_body(self, table, object_id, body):
        for ref in self.store.inherited_refs(table, object_id):
            try:
                self.sources.get(ref["source_id"])
            except DomainError:
                return {"id": object_id, "redacted": True, "reason": "source_policy_denied"}
        return body

    def objects(self, table, work_id):
        return [
            self.allowed_body(table, r["id"], json.loads(r["body"]))
            for r in self.db.execute(
                f"SELECT id,body FROM {table} WHERE work_id=? ORDER BY rowid", (work_id,)
            )
        ]

    def work(self, work_id):
        body = self.store.body("works", work_id)
        return self.allowed_body("works", work_id, body)

    def work_open(self, args):
        if "work_id" in args:
            body = self.work(args["work_id"])
            if self.config.contract_version == "2.0":
                from .projection import work_summary

                body = work_summary(body)
            return {
                **body,
                "decisions": self.objects("decisions", args["work_id"]),
                "issues": self.objects("issues", args["work_id"]),
            }
        create = args["create"]
        wid, eid = uid("work"), uid("event")
        body = dict(
            work_id=wid,
            title=create["title"],
            goal=create["goal"],
            scope=self.scope_defaults(create["scope"]),
            origin=create["origin"],
            provenance="agent_reported",
            origin_event_id=eid,
            revision=1,
            status="active",
            created_at=now(),
            updated_at=now(),
            completion_coverage="incomplete",
        )
        self.db.execute("INSERT INTO works VALUES(?,?,?)", (wid, 1, dumps(body)))
        event = dict(
            event_id=eid,
            kind="work_created",
            create=create,
            provenance="agent_reported",
            work_revision=1,
        )
        self.db.execute("INSERT INTO events VALUES(?,?,?,?)", (eid, wid, 1, dumps(event)))
        refs = create["origin"].get("source_refs", [])
        self.store.refs("works", wid, refs)
        self.store.refs("events", eid, refs)
        self.store.bump()
        return body

    def work_record(self, args):
        wid = args["work_id"]
        body = self.store.body("works", wid)
        if body["revision"] != args["expected_revision"]:
            raise DomainError("revision_conflict", "Work revision changed")
        record = _Record(self, wid, body, json.loads(dumps(args["event"])))
        handler = RECORD_HANDLERS.get(record.event["kind"])
        if handler:
            getattr(self, handler)(record)
        event, result = record.event, record.result
        if self.config.contract_version == "2.0" and event["kind"] != "completion_reported":
            body["completion_coverage"] = "incomplete"
        body.update(revision=result["revision"], updated_at=now())
        event.update(
            event_id=record.eid,
            work_revision=body["revision"],
            provenance="agent_reported",
            created_at=now(),
        )
        self.db.execute(
            "INSERT INTO events VALUES(?,?,?,?)", (record.eid, wid, body["revision"], dumps(event))
        )
        self.store.refs("events", record.eid, record.refs)
        self.db.execute(
            "UPDATE works SET revision=?,body=? WHERE id=?", (body["revision"], dumps(body), wid)
        )
        self.db.execute("UPDATE packets SET status='stale' WHERE work_id=?", (wid,))
        self.store.bump()
        result["provenance"] = "agent_reported"
        return result

    def _record_decision_proposed(self, record):
        record.insert(
            "decisions",
            "decision",
            dict(text=record.event["text"], status="proposed", source_refs=record.refs),
        )

    def _record_decision_transition(self, record):
        event, kind = record.event, record.event["kind"]
        decision = self.store.body("decisions", event["decision_id"], record.wid)
        if decision.get("redacted"):
            raise DomainError("input_incomplete", "Decision evidence was deleted")
        expected = "proposed" if kind == "decision_adopted" else "adopted_reported"
        if decision["status"] != expected:
            raise DomainError("invalid_argument", "Invalid decision state transition")
        record.refs += self.store.inherited_refs("decisions", event["decision_id"])
        if kind == "decision_superseded":
            replacement = self.store.body("decisions", event["replacement_id"], record.wid)
            if (
                replacement.get("status") != "adopted_reported"
                or replacement["id"] == decision["id"]
            ):
                raise DomainError(
                    "invalid_argument", "Replacement must be another adopted decision"
                )
            decision.update(status="superseded", replacement_id=replacement["id"])
            record.refs += self.store.inherited_refs("decisions", replacement["id"])
        else:
            decision.update(status="adopted_reported", origin=event["origin"])
        self.store.put_body("decisions", decision["id"], decision)
        self.store.refs("decisions", decision["id"], record.refs)

    def _record_scope_revised(self, record):
        event, body = record.event, record.body
        if "goal" in event:
            body["goal"] = event["goal"]
        body.update(
            scope=self.scope_defaults(event["scope"]),
            origin=event["origin"],
            origin_event_id=record.eid,
        )
        self.store.refs("works", record.wid, record.refs)

    def _record_issue_opened(self, record):
        event = record.event
        record.insert(
            "issues",
            "issue",
            dict(
                text=event["text"],
                kind=event["issue_kind"],
                status="open",
                source_refs=record.refs,
            ),
        )

    def _record_issue_resolved(self, record):
        issue = self.store.body("issues", record.event["issue_id"], record.wid)
        if issue.get("status") != "open":
            raise DomainError("invalid_argument", "Issue is not open")
        record.refs += self.store.inherited_refs("issues", issue["id"])
        issue.update(status="resolved_reported", resolution=record.event["resolution"])
        self.store.put_body("issues", issue["id"], issue)
        self.store.refs("issues", issue["id"], record.refs)

    def _record_evidence_reported(self, record):
        event = record.event
        role = (
            {"role": event.get("role", "support")} if self.config.contract_version == "2.0" else {}
        )
        record.insert(
            "evidence",
            "evidence",
            dict(
                claim=event["claim"],
                target_revision=event["target_revision"],
                observation=event["observation"],
                **role,
            ),
        )

    def _record_criterion(self, record):
        from .completion import record_criterion

        record.refs += record_criterion(self, record.body, record.event)
        self.store.refs("works", record.wid, record.refs)

    def _record_progress_reported(self, record):
        event = record.event
        record.body.update(
            progress=dict(summary=event["summary"], next_actions=event["next_actions"])
        )
        self.store.refs("works", record.wid, record.refs)

    def _record_completion_reported(self, record):
        event, body = record.event, record.body
        for evidence_id in event["evidence_ids"]:
            self.store.body("evidence", evidence_id, record.wid)
            record.refs += self.store.inherited_refs("evidence", evidence_id)
        body.update(
            status="completion_reported",
            completion_coverage="incomplete",
            completion_reason="Required acceptance criteria are not independently verified",
            completion_summary=event["summary"],
            evidence_ids=event["evidence_ids"],
            open_items=event["open_items"],
        )
        if self.config.contract_version == "2.0":
            from .completion import coverage

            body.update(
                progress={"summary": event["summary"], "next_actions": []},
                completion_coverage=coverage(self, body, event["target_revision"]),
                completion_target_revision=event["target_revision"],
                completion_reason="Coverage uses agent-reported evidence; it is not independent host verification",
            )
        self.store.refs("works", record.wid, record.refs)

    def _record_work_reopened(self, record):
        if record.body["status"] != "completion_reported":
            raise DomainError("invalid_argument", "Only a reported-complete work can be reopened")
        record.body.update(status="active", completion_coverage="incomplete")
        if self.config.contract_version == "2.0":
            record.body["progress"] = {"summary": record.event["reason"], "next_actions": []}

    def paginate(self, items, args, revision, scope, default):
        offset = 0
        if "cursor" in args:
            try:
                cursor = json.loads(base64.urlsafe_b64decode(args["cursor"].encode()))
                if cursor["scope"] != scope or cursor["revision"] != revision:
                    raise DomainError("cursor_stale", "List changed; start a fresh listing")
                offset = cursor["offset"]
                if type(offset) is not int or offset < 0:
                    raise ValueError()
            except (ValueError, KeyError, TypeError) as exc:
                raise DomainError("invalid_argument", "Invalid cursor") from exc
        limit = args.get("limit", default)
        next_offset = offset + limit
        cursor = (
            base64.urlsafe_b64encode(
                dumps(dict(scope=scope, revision=revision, offset=next_offset)).encode()
            ).decode()
            if next_offset < len(items)
            else None
        )
        return dict(items=items[offset:next_offset], next_cursor=cursor, list_revision=revision)

    def workspace_status(self, args):
        if self.engine and getattr(self.engine, "profile", {}).get("family") == "openjev_modal":
            self.engine.prepare()
        meta = self.store.meta()
        items = []
        for row in self.db.execute("SELECT id,body FROM works ORDER BY rowid DESC"):
            work = self.allowed_body("works", row["id"], json.loads(row["body"]))
            items.append(
                {
                    k: work[k]
                    for k in ("work_id", "title", "goal", "revision", "updated_at", "redacted")
                    if k in work
                }
            )
            items[-1]["unresolved_conflicts"] = sum(
                i.get("status") == "open" and i.get("kind") == "conflict"
                for i in self.objects("issues", row["id"])
            )
        source_count = self.db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        stale, checked, stale_count = stale_file_sources(self.config, self.db)
        result = {
            **self.paginate(items, args, meta["data_revision"], "works", 10),
            "project_id": self.config.project_id,
            "project_root": str(self.config.project_root),
            "data_revision": meta["data_revision"],
            "source_set_revision": meta["source_set_revision"],
            "sources": source_count,
            # Registered files whose bytes differ from the stored revision; sync these first.
            "stale_sources": {
                "checked": checked,
                "count": stale_count,
                "items": stale,
                "truncated": stale_count > len(stale),
            },
            "last_observed_at": self.db.execute("SELECT MAX(observed_at) FROM sources").fetchone()[
                0
            ],
            "engine": {
                "state": self.engine.state
                if self.engine
                else (
                    "disabled"
                    if self.config.engine.get("state", "disabled") == "disabled"
                    else "unavailable"
                ),
                "reason": "Observation only; no evaluated active profile",
            },
            "features": [
                "work_memory",
                "lexical_search",
                "korean_substring",
                "fixed_revision_read",
                "protected_context",
            ],
            "read_only": self.config.read_only,
            "mutation_retention": "at_least_30_days_do_not_reuse_ids",
        }
        if self.config.contract_version == "2.0":
            from .judgment import promotion

            result["engine"].update(
                family=getattr(self.engine, "profile", {}).get("family") if self.engine else None,
                configured_mode=self.config.engine.get("state", "disabled"),
                profile_fingerprint=self.engine.fingerprint if self.engine else None,
                promotion={
                    p: promotion(self, p, "ko")[0]
                    for p in ("relevance", "evidence_relation", "presentation", "capability_fit")
                },
                reason="Actual process state and per-purpose Korean evaluation gates",
                preparation_error=self.config.engine.get("preparation_error"),
            )
            result["features"] += [
                "capability_recommendation",
                "read_handoff",
                "completion_criteria",
                "whole_envelope_budget",
            ]
            result["host_integration"] = {
                "stdio": "implemented",
                "desktop_current_session": "not_observed",
                "hooks": "not_installed",
                "write_handoff": "unsupported",
            }
        if self.engine and hasattr(self.engine, "snapshot"):
            result["engine"].update(self.engine.snapshot())
        return result

    def work_inspect(self, args):
        work = self.store.body("works", args["work_id"])
        view = args["view"]
        if view == "judgments":
            if "packet_id" not in args:
                raise DomainError("invalid_argument", "packet_id required for judgment inspection")
            packet = self.allowed_body(
                "packets",
                args["packet_id"],
                self.store.body("packets", args["packet_id"], args["work_id"]),
            )
            row = self.db.execute(
                "SELECT status FROM packets WHERE id=?", (args["packet_id"],)
            ).fetchone()
            return {
                **self.paginate(
                    packet.get("judgment", {}).get("evaluations", []),
                    args,
                    work["revision"],
                    f"{args['work_id']}:{args['packet_id']}:judgments",
                    5,
                ),
                "packet_status": row["status"],
            }
        if view == "criteria":
            body = self.work(args["work_id"])
            return self.paginate(
                list(body.get("criteria", {}).values()),
                args,
                work["revision"],
                f"{args['work_id']}:criteria",
                20,
            )
        if view == "mutation":
            if "mutation_id" not in args:
                raise DomainError("invalid_argument", "mutation_id required for mutation view")
            rows = self.db.execute(
                "SELECT tool,state,result FROM mutations WHERE id=? AND work_id=?",
                (args["mutation_id"], args["work_id"]),
            ).fetchall()
            if not rows:
                raise DomainError("not_found", "Mutation not found in this work")
            return {
                "mutations": [
                    dict(
                        tool=r["tool"],
                        state=r["state"],
                        result=json.loads(r["result"]) if r["result"] else None,
                    )
                    for r in rows
                ]
            }
        return self.paginate(
            self.objects(view, args["work_id"]),
            args,
            work["revision"],
            f"{args['work_id']}:{view}",
            20,
        )

    def source_read(self, args):
        return self.sources.read(args)

    def sync(self, args):
        deadline = time.monotonic() + self.config.timeout_seconds
        key = digest(args["mutation_id"].encode())
        with (
            FileLock(self.config.db_path.parent / "ingestion.lock"),
            FileLock(self.config.db_path.parent / "locks" / (key + ".lock")),
        ):
            with self.store.transaction():
                replay = self.mutation_start("source_sync", args)
                if replay is not None:
                    return replay
            processed, unprocessed = [], []
            for index, item in enumerate(args["items"]):
                old = self.db.execute(
                    "SELECT result FROM sync_items WHERE mutation_id=? AND item_index=?",
                    (args["mutation_id"], index),
                ).fetchone()
                if old:
                    processed.append(json.loads(old[0]))
                    continue
                if time.monotonic() >= deadline:
                    unprocessed.extend(range(index, len(args["items"])))
                    break
                try:
                    prepared = self.sources.prepare(item)
                    with self.store.transaction():
                        item_result = dict(index=index, outcome="ok", **self.sources.save(prepared))
                        self.db.execute(
                            "INSERT INTO sync_items VALUES(?,?,?)",
                            (args["mutation_id"], index, dumps(item_result)),
                        )
                except DomainError as exc:
                    item_result = dict(
                        index=index, outcome="error", error=dict(code=exc.code, message=exc.message)
                    )
                    with self.store.transaction():
                        self.db.execute(
                            "INSERT INTO sync_items VALUES(?,?,?)",
                            (args["mutation_id"], index, dumps(item_result)),
                        )
                processed.append(item_result)
            result = response(
                args["request_id"],
                dict(items=processed, unprocessed=unprocessed, snapshot="per_source_observation"),
                outcome="partial"
                if unprocessed or any(i["outcome"] != "ok" for i in processed)
                else "ok",
            )
            with self.store.transaction():
                self.mutation_finish("source_sync", args, result)
            return result

    def data_forget(self, args):
        if self.store.meta()["data_revision"] != args["expected_data_revision"]:
            raise DomainError(
                "revision_conflict", "Data changed; review current scope before deleting"
            )
        target = args["target"]
        kind, object_id = target["kind"], target.get("source_id", target.get("work_id"))
        invalidated = self.db.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
        affected_works = set()
        if kind == "source":
            self.sources.get(object_id)
            owners = self.db.execute(
                "SELECT DISTINCT owner_type,owner_id FROM source_refs WHERE source_id=?",
                (object_id,),
            ).fetchall()
            for owner in owners:
                table, oid = owner
                if table in {"events", "decisions", "issues", "evidence"}:
                    row = self.db.execute(
                        f"SELECT work_id FROM {table} WHERE id=?", (oid,)
                    ).fetchone()
                    if row:
                        affected_works.add(row[0])
                        old = self.store.body(table, oid)
                        redacted = {
                            k: old[k]
                            for k in (
                                "id",
                                "event_id",
                                "work_revision",
                                "kind",
                                "status",
                                "provenance",
                            )
                            if k in old
                        }
                        redacted.update(redacted=True, reason="source_deleted")
                        self.store.put_body(table, oid, redacted)
                elif table == "works":
                    body = self.store.body("works", oid)
                    body.update(
                        goal="[source deleted]",
                        title="[source deleted]",
                        origin={"quote": "[source deleted]"},
                        scope={
                            "mode": "investigate",
                            "allowed_actions": [],
                            "constraints": ["Source deleted; scope requires review"],
                        },
                    )
                    for field in ("progress", "completion_summary", "open_items", "criteria"):
                        body.pop(field, None)
                    body["completion_coverage"] = "incomplete"
                    self.store.put_body("works", oid, body)
                    affected_works.add(oid)
            for index in ("lexical", "trigrams"):
                self.db.execute(
                    f"DELETE FROM {index} WHERE chunk_id IN (SELECT id FROM chunks WHERE source_id=?)",
                    (object_id,),
                )
            self.db.execute("DELETE FROM sources WHERE id=?", (object_id,))
            self.store.bump(source=True)
        else:
            self.store.body("works", object_id)
            for table in ("events", "decisions", "issues", "evidence"):
                self.db.execute(
                    f"DELETE FROM source_refs WHERE owner_type=? AND owner_id IN (SELECT id FROM {table} WHERE work_id=?)",
                    (table, object_id),
                )
            self.db.execute(
                "DELETE FROM source_refs WHERE owner_type='works' AND owner_id=?", (object_id,)
            )
            self.db.execute("DELETE FROM works WHERE id=?", (object_id,))
            self.store.bump()
        for wid in affected_works:
            body = self.store.body("works", wid)
            body["completion_coverage"] = "incomplete"
            body["revision"] += 1
            body["updated_at"] = now()
            self.db.execute(
                "UPDATE works SET body=?,revision=? WHERE id=?",
                (dumps(body), body["revision"], wid),
            )
        # All packets and replay bodies are small derived caches; conservative invalidation
        # avoids leaking indirect summaries after linked evidence is deleted.
        self.db.execute("DELETE FROM packets")
        self.db.execute("DELETE FROM source_refs WHERE owner_type='packets'")
        self.db.execute("DELETE FROM sync_items")
        self.store.redact_replays("data_deleted")
        self.db.execute("INSERT OR REPLACE INTO tombstones VALUES(?,?,?)", (kind, object_id, now()))
        return dict(
            deleted=1,
            invalidated_packets=invalidated,
            data_revision=self.store.meta()["data_revision"],
            retained_shared_sources=self.db.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            physical_erasure_guaranteed=False,
            original_files_unchanged=True,
        )

    def context_prepare(self, args):
        from .context import prepare_context

        return prepare_context(self, args)
