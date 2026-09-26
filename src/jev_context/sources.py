from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import defaultdict

from .common import DomainError, digest, dumps, now, uid


def stale_file_sources(config, db, limit=50, scan=2000):
    """Registered files whose bytes or availability differ from their stored state.

    Returns (items, checked, stale): up to `limit` items of source ID, relative path and reason
    ("changed", or the read error code such as "not_found"), plus the scanned and stale counts.
    """
    rows = db.execute(
        "SELECT s.id, s.locator, r.sha256, s.status FROM sources s LEFT JOIN source_revisions r "
        "ON r.id = s.current_revision WHERE s.kind = 'file' "
        "ORDER BY s.locator LIMIT ?",
        (scan,),
    ).fetchall()
    items, stale = [], 0
    for source_id, locator, sha256, status in rows:
        try:
            _, raw, _ = config.read_file(locator)
            reason = None if digest(raw) == sha256 and status == "available" else "changed"
        except DomainError as exc:
            reason = exc.code
        if reason:
            stale += 1
            if len(items) < limit:
                items.append({"source_id": source_id, "relative_path": locator, "reason": reason})
    return items, len(rows), stale


def normalized(text):
    return unicodedata.normalize("NFC", text).casefold()


def split_chunks(text, revision, source_id):
    lines = text.splitlines(keepends=True)
    parts, start, offset, buffer = [], 1, 0, ""
    for line_number, line in enumerate(lines, 1):
        if buffer and (len((buffer + line).encode()) > 4096 or line.startswith("#")):
            parts.append((start, line_number - 1, offset, buffer))
            offset += len(buffer.encode())
            start, buffer = line_number, ""
        # A long line remains intact for source_read, but chunks split by characters and
        # retain byte offsets. Context marks these as fragments, not complete lines.
        if len(line.encode()) > 4096:
            current = ""
            for char in line:
                if len((current + char).encode()) > 4096:
                    parts.append((line_number, line_number, offset, current))
                    offset += len(current.encode())
                    current = ""
                current += char
            if current:
                parts.append((line_number, line_number, offset, current))
                offset += len(current.encode())
            start, buffer = line_number + 1, ""
        else:
            buffer += line
    if buffer:
        parts.append((start, len(lines), offset, buffer))
    ids = [f"chunk-{digest(f'{revision}:{i}'.encode())[:32]}" for i in range(len(parts))]
    for i, (first, last, byte_start, content) in enumerate(parts):
        yield (
            ids[i],
            source_id,
            revision,
            first,
            last,
            byte_start,
            byte_start + len(content.encode()),
            content,
            normalized(content),
            dumps(ids[max(0, i - 1) : i] + ids[i + 1 : i + 2]),
        )


class Sources:
    def __init__(self, store):
        self.store, self.db, self.config = store, store.db, store.config

    def permitted(self, source):
        if source["kind"] == "file":
            self.config.check_path(source["locator"])

    def get(self, source_id):
        row = self.db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise DomainError("not_found", "Source not found")
        self.permitted(row)
        return dict(row)

    def prepare(self, item):
        if item["kind"] == "file":
            try:
                locator, raw, text = self.config.read_file(item["relative_path"])
            except DomainError as exc:
                if exc.code == "not_found":
                    path = self.config.check_path(item["relative_path"])
                    locator = path.resolve().relative_to(self.config.project_root).as_posix()
                    return dict(
                        kind="file",
                        locator=locator,
                        status="missing",
                        origin={"relative_path": locator},
                    )
                raise
            origin = {"relative_path": locator, "provenance": "service_observed"}
        else:
            locator, text = item["external_key"], item["text"]
            raw = text.encode()
            if len(raw) > 8192:
                raise DomainError("invalid_argument", "Excerpt exceeds 8KiB")
            origin = {k: v for k, v in item.items() if k not in {"text", "kind", "external_key"}}
            origin.update(provenance="agent_reported", origin_kind="supplied_excerpt")
        return dict(
            kind=item["kind"],
            locator=locator,
            raw=raw,
            text=text,
            origin=origin,
            status="available",
        )

    def save(self, prepared):
        """Caller holds a short transaction; no file I/O here."""
        p = prepared
        existing = self.db.execute(
            "SELECT * FROM sources WHERE kind=? AND locator=?", (p["kind"], p["locator"])
        ).fetchone()
        sid = existing["id"] if existing else uid("source")
        if existing and p["kind"] == "excerpt":
            original = json.loads(existing["origin"])
            if any(original.get(k) != p["origin"].get(k) for k in ("origin_label", "origin_url")):
                raise DomainError(
                    "invalid_argument", "external_key is already bound to another origin"
                )
        observed = now()
        if not existing:
            self.db.execute(
                "INSERT INTO sources VALUES(?,?,?,?,?,?,?)",
                (sid, p["kind"], p["locator"], None, p["status"], dumps(p["origin"]), observed),
            )
        if p["status"] == "missing":
            if not existing or existing["status"] != "missing":
                self.store.bump(source=True)
            self.db.execute(
                "UPDATE sources SET status='missing',observed_at=? WHERE id=?", (observed, sid)
            )
            return dict(
                source_id=sid,
                status="missing",
                current_revision=existing["current_revision"] if existing else None,
            )
        sha = digest(p["raw"])
        revision_row = self.db.execute(
            "SELECT id FROM source_revisions WHERE source_id=? AND sha256=?", (sid, sha)
        ).fetchone()
        revision = revision_row[0] if revision_row else uid("rev")
        if not revision_row:
            self.store.check_space(len(p["raw"]) * 12 + 16384)
            self.db.execute(
                "INSERT INTO source_revisions VALUES(?,?,?,?,?,?,?)",
                (revision, sid, sha, p["raw"], p["text"], dumps(p["origin"]), observed),
            )
            for chunk in split_chunks(p["text"], revision, sid):
                self.db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?)", chunk)
                for index in ("lexical", "trigrams"):
                    self.db.execute(
                        f"INSERT INTO {index}(chunk_id,text) VALUES(?,?)", (chunk[0], chunk[8])
                    )
        if (
            not existing
            or existing["current_revision"] != revision
            or existing["status"] != "available"
        ):
            self.store.bump(source=True)
        self.db.execute(
            "UPDATE sources SET current_revision=?,status='available',origin=?,observed_at=? WHERE id=?",
            (revision, dumps(p["origin"]), observed, sid),
        )
        return dict(
            source_id=sid,
            revision=revision,
            content_hash=sha,
            status="available",
            provenance=p["origin"]["provenance"],
        )

    def revision(self, source_id, revision):
        source = self.get(source_id)
        row = self.db.execute(
            "SELECT * FROM source_revisions WHERE source_id=? AND id=?", (source_id, revision)
        ).fetchone()
        if row is None:
            raise DomainError("not_found", "Source revision not found")
        return source, dict(row)

    def read(self, args):
        source, revision = self.revision(args["source_id"], args["revision"])
        start, limit = args.get("start_line", 1), args.get("max_bytes", 8192)
        lines = revision["text"].splitlines(keepends=True)
        if start > len(lines) + 1:
            raise DomainError("invalid_argument", "Start line is outside the source")
        selected, size = [], 0
        for line in lines[start - 1 :]:
            length = len(line.encode())
            if size + length > limit:
                if not selected:
                    raise DomainError("line_too_large", "A complete line exceeds max_bytes")
                break
            selected.append(line)
            size += length
        next_line = start + len(selected)
        return dict(
            source_id=source["id"],
            revision=revision["id"],
            text="".join(selected),
            content_hash=revision["sha256"],
            start_line=start,
            end_line=next_line - 1,
            next_start_line=next_line,
            eof=next_line > len(lines),
            current_revision=source["current_revision"],
            freshness="last_observed"
            if revision["id"] == source["current_revision"]
            else "historical",
            source_status=source["status"],
            origin_kind="file" if source["kind"] == "file" else "supplied_excerpt",
            origin=json.loads(revision["origin"]),
        )

    def search(self, query, source_ids, deadline):
        tokens = list(dict.fromkeys(re.findall(r"[\w]+", normalized(query))))[:32]
        sources, excluded = {}, []
        for row in self.db.execute("SELECT * FROM sources WHERE status='available'"):
            if source_ids is not None and row["id"] not in source_ids:
                continue
            try:
                self.permitted(row)
                sources[row["id"]] = dict(row)
            except DomainError:
                excluded.append(row["id"])
        rows = {}
        unsearched = []
        # A bounded scan also covers 1-2 character Korean words and exact paths.
        for source in sources.values():
            if time.monotonic() >= deadline:
                unsearched.append(source["id"])
                continue
            for row in self.db.execute(
                "SELECT * FROM chunks WHERE revision=? ORDER BY id", (source["current_revision"],)
            ):
                if time.monotonic() >= deadline:
                    unsearched.append(source["id"])
                    break
                rows[row["id"]] = dict(row)
        channels = []
        channels.append(
            sorted(
                (
                    (
                        sum(t in normalized(sources[r["source_id"]]["locator"]) for t in tokens),
                        r["id"],
                    )
                    for r in rows.values()
                ),
                key=lambda p: (-p[0], p[1]),
            )
        )
        channels.append(
            sorted(
                ((sum(t in r["search_text"] for t in tokens), r["id"]) for r in rows.values()),
                key=lambda p: (-p[0], p[1]),
            )
        )
        for index in ("lexical", "trigrams"):
            terms = [t for t in tokens if index == "lexical" or len(t) >= 3]
            if terms and time.monotonic() < deadline:
                expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
                matches = self.db.execute(
                    f"SELECT chunk_id,bm25({index}) FROM {index} WHERE {index} MATCH ? ORDER BY bm25({index}),chunk_id LIMIT 1000",
                    (expr,),
                ).fetchall()
                channels.append([(1, r[0]) for r in matches if r[0] in rows])
        scores = defaultdict(float)
        for channel in channels:
            rank = 0
            for match, chunk_id in channel:
                if match:
                    rank += 1
                    scores[chunk_id] += 1 / (60 + rank)
        ranked = sorted(scores, key=lambda cid: (-scores[cid], cid))[:40]
        return [rows[cid] for cid in ranked], dict(
            scope="registered_sources",
            candidates=len(scores),
            searched_source_ids=sorted(sources),
            excluded=excluded,
            unsearched=sorted(set(unsearched)),
            claim="Only registered, permitted sources searched; unregistered and new counterevidence remain unknown",
            snapshot="per_source_observation",
        )
