"""Experimental, outside design 2.0: shrink large shell output to the lines a request needs.

Not registered in .codex/hooks.json; the design hooks are in session_hooks.py. Kept as the
evidence behind docs/hook-benchmark-plan.md.

UserPromptSubmit records the request text; PostToolUse replaces an oversized shell result with
selected lines and the path of the saved full output. Any failure passes the result through.
Run as ``python -m jev_context.tool_hooks --state-dir DIR [--mode observe|filter]``;
``DIR/settings.json`` may override mode, min_bytes and budget_bytes. Invalid settings pass through.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import uuid
from pathlib import Path

# Codex Desktop runs shell commands inside the code-mode "exec" tool (observed in 0.155 rollouts).
SHELL_TOOLS = {"bash", "shell", "local_shell", "exec", "exec_command", "unified_exec", "powershell"}
SIGNAL = re.compile(
    r"error|fail|exception|traceback|panic|fatal|warn|assert|denied|not found|오류|실패|경고",
    re.IGNORECASE,
)
SAVED_OUTPUT = re.compile(r"hook_outputs|outputs[\\/]+[^\s'\"]*[0-9a-f]{32}\.txt", re.IGNORECASE)
WORD = re.compile(r"[0-9A-Za-z_][0-9A-Za-z_.\-/]*[0-9A-Za-z_]|[가-힣]{2,}")
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "then", "해줘", "해주세요",
    "그리고", "있는", "하는", "이거", "그거", "다시", "먼저",
}  # fmt: skip
GREP_LINE = re.compile(r"^(?:[A-Za-z]:)?[^:\s][^:]*:\d+:")
# (head, tail, context lines around a match) for plain output and for path:line: search output.
SHAPES = {"plain": (5, 15, 1), "grep": (2, 2, 0)}
COMMON = 0.5  # a term on more than this share of lines carries no selection signal


def terms(*texts, parts=True):
    """Lowercased words; with parts, also the pieces of path-like words (service.py -> service)."""
    found = set()
    for text in texts:
        for word in WORD.findall(text or ""):
            word = word.lower()
            if len(word) < 2 or word in STOPWORDS:
                continue
            found.add(word)
            if parts and ("/" in word or "." in word):
                found.update(p for p in re.split(r"[./\\-]", word) if len(p) >= 3)
            if re.fullmatch(r"[가-힣]+", word) and len(word) >= 3:
                found.add(word[:-1])  # drop a trailing particle such as 을/를/의
    return found


def response_text(value):
    """The model-facing text of a tool result whose exact shape the docs leave open."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # Code-mode results are input_text items; command output sits in an embedded JSON chunk.
        parts = []
        for item in value:
            text = item.get("text") if isinstance(item, dict) else item
            if not isinstance(text, str):
                continue
            try:
                inner = json.loads(text) if text.lstrip().startswith("{") else None
            except ValueError:
                inner = None
            parts.append(inner["output"] if isinstance(inner, dict) and "output" in inner else text)
        return "\n".join(str(part) for part in parts)
    if isinstance(value, dict):
        for key in ("output", "aggregated_output", "formatted_output", "content", "text"):
            if isinstance(value.get(key), str):
                return value[key]
        parts = [value[k] for k in ("stdout", "stderr") if isinstance(value.get(k), str)]
        if parts:
            return "\n".join(p for p in parts if p)
    return json.dumps(value, ensure_ascii=False)


def command_text(tool_input):
    if isinstance(tool_input, dict):
        command = tool_input.get("command", tool_input.get("cmd", ""))
        return " ".join(command) if isinstance(command, list) else str(command)
    return str(tool_input or "")


def shape(lines):
    filled = [line for line in lines if line.strip()]
    grep = filled and sum(bool(GREP_LINE.match(line)) for line in filled) > 0.6 * len(filled)
    return "grep" if grep else "plain"


def select(lines, request_terms, budget, kind="plain", command_terms=()):
    """Choose line indexes: head, tail, then relevant lines by rarity-weighted term score.

    Returns (chosen indexes, relevant line indexes). Relevant means a request
    term or an error signal matched; command terms only rank lines at half weight, because a
    command's own pattern describes every line it printed. A term on most lines is ignored.
    """
    head, tail, context = SHAPES[kind]
    lowered = [line.lower() for line in lines]

    def weights(words, factor):
        found = {}
        for word in words:
            if len(word) < 3:  # two-letter substrings such as "rg" hit unrelated words
                continue
            count = sum(1 for line in lowered if word in line)
            if count and count <= COMMON * len(lines):
                found[word] = factor * math.log(len(lines) / count)
        return found

    request = weights(request_terms, 1.0)
    command = weights(set(command_terms) - set(request), 0.5)
    scored, relevant = [], set()
    for index, line in enumerate(lowered):
        score = sum(weight for word, weight in request.items() if word in line)
        if SIGNAL.search(lines[index]):
            score += 2
        if score:
            relevant.add(index)
        score += sum(weight for word, weight in command.items() if word in line)
        if score:
            scored.append((-score, index))
    chosen = set(range(min(head, len(lines))))
    chosen |= set(range(max(0, len(lines) - tail), len(lines)))
    used = sum(len(lines[i].encode()) + 8 for i in chosen)
    for _, index in sorted(scored):
        window = range(max(0, index - context), min(len(lines), index + context + 1))
        extra = [i for i in window if i not in chosen]
        cost = sum(len(lines[i].encode()) + 8 for i in extra)
        if used + cost > budget:
            continue
        chosen.update(extra)
        used += cost
    return sorted(chosen), relevant


def omitted(lines, start, end, kind, relevant=frozenset()):
    """Marker for lines[start:end]; it names search-output files and says whether the gap hides
    request matches, so the model can trust that a shown block is complete without rereading."""
    missed = sum(1 for index in range(start, end) if index in relevant)
    note = f"... ({end - start} lines omitted, " + (
        f"{missed} of them match the request" if missed else "none match the request"
    )
    if kind == "grep":
        counts = {}
        for line in lines[start:end]:
            match = GREP_LINE.match(line)
            name = re.split(r"[\\/]", match.group(0).rsplit(":", 2)[0])[-1] if match else "other"
            counts[name] = counts.get(name, 0) + 1
        shown = sorted(counts.items(), key=lambda item: -item[1])
        files = ", ".join(f"{name} x{count}" for name, count in shown[:4])
        note += f": {files}" + (f", +{len(shown) - 4} more files" if len(shown) > 4 else "")
    return note + ")"


def render(lines, chosen, header, kind="plain", relevant=frozenset()):
    out, previous = [header], -1
    for index in chosen:
        if index != previous + 1:
            out.append(omitted(lines, previous + 1, index, kind, relevant))
        out.append(f"{index + 1}: {lines[index]}")
        previous = index
    if previous != len(lines) - 1:
        out.append(omitted(lines, previous + 1, len(lines), kind, relevant))
    return "\n".join(out)


def safe_id(value):
    return re.sub(r"[^0-9A-Za-z_-]", "_", str(value or "unknown"))[:80]


def log(state, record):
    record["at"] = time.time()
    with open(state / "hook-log.jsonl", "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def handle(payload, state, mode="filter", min_bytes=24000, budget_bytes=8000):
    """Return the hook's stdout JSON object, or None to pass the result through unchanged."""
    event = payload.get("hook_event_name")
    session = safe_id(payload.get("session_id"))
    prompts = state / "prompts"
    if event == "UserPromptSubmit":
        prompts.mkdir(parents=True, exist_ok=True)
        (prompts / f"{session}.json").write_text(
            json.dumps({"turn_id": payload.get("turn_id"), "prompt": payload.get("prompt", "")}),
            encoding="utf-8",
        )
        return None
    if event != "PostToolUse":
        return None
    tool = str(payload.get("tool_name", ""))
    raw = payload.get("tool_response")
    text = response_text(raw)
    size = len(text.encode())
    command = command_text(payload.get("tool_input"))
    record = {
        "event": event,
        "tool": tool,
        "response_type": type(raw).__name__,
        "response_keys": sorted(raw)[:20] if isinstance(raw, dict) else None,
        "input_type": type(payload.get("tool_input")).__name__,
        "bytes": size,
        "command": command[:300],
        "mode": mode,
    }
    outputs = state / "outputs"
    if (
        tool.lower() not in SHELL_TOOLS
        or size < min_bytes
        # Reading a saved full output (ours or Codex's spilled hook output) is never shortened.
        or SAVED_OUTPUT.search(command)
    ):
        log(state, {**record, "action": "pass"})
        return None
    prompt = ""
    try:
        prompt = json.loads((prompts / f"{session}.json").read_text(encoding="utf-8"))["prompt"]
    except (OSError, ValueError, KeyError):
        pass
    lines = text.splitlines()
    kind = shape(lines)
    # Whole request words decide relevance; path pieces and command words only rank lines.
    exact = terms(prompt, parts=False)
    ranking = (terms(prompt) - exact) | terms(command)
    chosen, matches = select(lines, exact, budget_bytes, kind, ranking)
    relevant, kept = len(matches), len(matches & set(chosen))
    saved = outputs / session / f"{uuid.uuid4().hex}.txt"
    if not relevant:
        coverage = "No line matched the request or error signals; only the head and tail are shown."
    elif kept == relevant:
        coverage = (
            f"All {relevant} lines matching the request terms or error signals are included, "
            "so rereading the full output should not be needed for them."
        )
    else:
        coverage = (
            f"Only {kept} of {relevant} lines matching the request terms or error signals fit; "
            "read the saved file for the rest."
        )
    header = (
        "[jev-context] The command ran; the jev-context hook only shortened its output "
        f"({len(lines)} lines / {size} bytes) to {len(chosen)} lines. {coverage}\n"
        f"Full output saved at: {saved}\n"
        "If the surrounding script is reported as failed, that is this hook, not the command; "
        "statements after this command in the same script may not have run.\n"
        "Read the saved file directly (for example a line range) instead of rerunning the command."
    )
    reason = render(lines, chosen, header, kind, matches)
    log(state, {**record, "action": mode, "shape": kind, "kept_lines": len(chosen),
                "lines": len(lines), "relevant_lines": relevant, "relevant_kept": kept,
                "replacement_bytes": len(reason.encode()), "saved": str(saved),
                "prompt_known": bool(prompt)})  # fmt: skip
    if mode != "filter":
        return None
    saved.parent.mkdir(parents=True, exist_ok=True)
    # newline="" keeps the output byte-exact; Windows text mode would turn \r\n into \r\r\n.
    saved.write_text(text, encoding="utf-8", newline="")
    return {"decision": "block", "reason": reason}


def settings(state, mode, min_bytes, budget_bytes):
    """Overrides from <state>/settings.json, so switching modes leaves the trusted hook unchanged."""
    try:
        values = json.loads((state / "settings.json").read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return mode, min_bytes, budget_bytes
    mode = values.get("mode", mode)
    min_bytes = values.get("min_bytes", min_bytes)
    budget_bytes = values.get("budget_bytes", budget_bytes)
    if (
        mode not in {"observe", "filter"}
        or type(min_bytes) is not int
        or type(budget_bytes) is not int
        or not 1000 <= budget_bytes <= min_bytes
    ):
        raise ValueError("invalid settings.json")
    return mode, min_bytes, budget_bytes


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jev-context-tool-hooks")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("observe", "filter"), default="observe")
    parser.add_argument("--min-bytes", type=int, default=24000)
    parser.add_argument("--budget-bytes", type=int, default=8000)
    args = parser.parse_args(argv)
    state = args.state_dir.resolve()
    try:
        state.mkdir(parents=True, exist_ok=True)
        mode, min_bytes, budget_bytes = settings(
            state, args.mode, args.min_bytes, args.budget_bytes
        )
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        result = handle(payload, state, mode, min_bytes, budget_bytes)
        if result:
            sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    except Exception as exc:  # A hook failure must never break the host's tool result.
        try:
            log(state, {"event": "error", "error": f"{type(exc).__name__}: {exc}"[:500]})
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
