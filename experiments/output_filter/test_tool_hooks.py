import json
import subprocess
import sys
from pathlib import Path

from experiments.output_filter.tool_hooks import handle, terms


def post(command, output, session="s1", tool="Bash"):
    return {
        "hook_event_name": "PostToolUse",
        "session_id": session,
        "turn_id": "t1",
        "tool_name": tool,
        "tool_input": {"command": command},
        "tool_response": output,
    }


def big_output(marker_line=4321, lines=5000):
    rows = [f"src/module_{i}.py:{i}: ordinary line {i}" for i in range(lines)]
    rows[marker_line] = "src/auth/session.py:88: def refreshToken(user): raise TokenExpired"
    return "\n".join(rows)


def test_small_or_non_shell_output_passes_through(tmp_path):
    assert handle(post("rg x", "short"), tmp_path) is None
    assert handle(post("read", big_output(), tool="mcp__fs__read"), tmp_path) is None
    assert handle({"hook_event_name": "Stop"}, tmp_path) is None


def test_large_output_keeps_request_lines_and_saves_full_text(tmp_path):
    handle(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "refreshToken 확인"},
        tmp_path,
    )
    text = big_output()
    result = handle(post("rg -n def", text), tmp_path, budget_bytes=4000)
    reason = result["reason"]
    assert result["decision"] == "block"
    assert "4322: src/auth/session.py:88: def refreshToken" in reason
    assert len(reason.encode()) < len(text.encode()) / 20
    saved = next((tmp_path / "outputs" / "s1").iterdir())
    assert saved.read_text(encoding="utf-8") == text
    assert str(saved) in reason
    logged = json.loads((tmp_path / "hook-log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert logged["prompt_known"] and logged["action"] == "filter"


def test_errors_and_tail_survive_without_a_prompt(tmp_path):
    rows = [f"PASSED test_{i}" for i in range(4000)]
    rows[1234] = "FAILED test_login - AssertionError: expected 200"
    rows.append("=== 1 failed, 3999 passed ===")
    reason = handle(post("pytest -q", "\n".join(rows)), tmp_path)["reason"]
    assert "AssertionError: expected 200" in reason
    assert "1 failed, 3999 passed" in reason


def test_observe_mode_logs_without_replacing(tmp_path):
    assert handle(post("rg -n def", big_output()), tmp_path, mode="observe") is None
    logged = json.loads((tmp_path / "hook-log.jsonl").read_text(encoding="utf-8"))
    assert logged["action"] == "observe" and logged["replacement_bytes"] < logged["bytes"]
    assert not (tmp_path / "outputs").exists()


def test_reading_saved_output_is_never_shortened_again(tmp_path):
    first = handle(post("rg -n def", big_output()), tmp_path)
    saved = next((tmp_path / "outputs" / "s1").iterdir())
    for command in (f"Get-Content '{saved}'", f"sed -n 1,5000p {saved.as_posix()}"):
        assert handle(post(command, big_output()), tmp_path) is None
    assert first


def test_object_shaped_response_is_read(tmp_path):
    response = {"stdout": big_output(), "stderr": "", "exit_code": 0}
    result = handle(post("rg -n def", response), tmp_path)
    assert "refreshToken" in result["reason"]


def test_codex_desktop_code_mode_exec_result_is_read(tmp_path):
    # Shape observed in Codex Desktop 0.155 rollouts: input_text items with an embedded chunk.
    chunk = {
        "chunk_id": "b09a39",
        "exit_code": 0,
        "original_token_count": 9,
        "output": big_output(),
    }
    response = [
        {"type": "input_text", "text": "Script completed\nWall time 4.1 seconds\nOutput:\n"},
        {"type": "input_text", "text": json.dumps(chunk)},
    ]
    code = 'text(await tools.exec_command({cmd:"rg -n def src"}));'
    result = handle({**post("", response, tool="exec"), "tool_input": code}, tmp_path)
    # The "Script completed" preamble shifts line numbers, so match the content only.
    assert "src/auth/session.py:88: def refreshToken" in result["reason"]
    assert '"chunk_id"' not in result["reason"]


def def_search_output():
    # The 2026-09-24 Desktop test: 134 `def` lines across src, 21 of them in service.py.
    rows = []
    for module, count in (("budget", 20), ("cli", 30), ("service", 21), ("storage", 63)):
        for i in range(count):
            rows.append(
                f"src\\jev_context\\{module}.py:{10 * i + 7}:    def {module}_fn_{i}(self, args):"
            )
    # Lines that only the command's own pattern (async) or "rg" inside "args" would match.
    rows += [f"src\\jev_context\\server.py:{i}:async def handler_{i}(args):" for i in range(8)]
    # A `service` parameter elsewhere matches the path piece "service" but not "service.py".
    rows += [f"src\\jev_context\\context.py:{i}:def prepare_{i}(service, args):" for i in range(12)]
    return "\n".join(rows)


def prompt(state, text, session="s1"):
    handle({"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": text}, state)


def test_rare_request_terms_win_over_the_searched_keyword(tmp_path):
    prompt(tmp_path, "src 전체에서 `def`를 rg로 찾아서 service.py에 어떤 함수가 있는지 알려줘")
    command = "rg -n '^\\s*(async\\s+)?def\\s+' src"
    result = handle(post(command, def_search_output()), tmp_path, min_bytes=4000, budget_bytes=3000)
    reason = result["reason"]
    kept = [line for line in reason.splitlines() if "service.py:" in line]
    assert len(kept) == 21
    assert "All 21 lines matching the request terms" in reason
    assert "not the command" in reason
    logged = json.loads((tmp_path / "hook-log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert logged["shape"] == "grep" and logged["relevant_kept"] == logged["relevant_lines"] == 21


def test_omitted_search_lines_name_their_files(tmp_path):
    # In the second Desktop test the model reread lines 61-90 only to learn whether the
    # "... (9 lines omitted)" after the service.py block hid more service.py lines.
    prompt(tmp_path, "service.py 함수")
    result = handle(post("rg -n def src", def_search_output()), tmp_path, min_bytes=4000)
    markers = [line for line in result["reason"].splitlines() if line.startswith("... (")]
    assert markers and all("service.py" not in marker for marker in markers)
    assert all("none match the request" in marker for marker in markers)
    assert any("storage.py x" in marker for marker in markers)


def test_omitted_marker_counts_hidden_matches(tmp_path):
    prompt(tmp_path, "service.py 함수")
    result = handle(post("rg -n def src", def_search_output()), tmp_path, min_bytes=4000,
                    budget_bytes=1000)  # fmt: skip
    assert "of them match the request" in result["reason"]


def test_saved_output_is_byte_exact_with_crlf(tmp_path):
    text = big_output(marker_line=100, lines=3000).replace("\n", "\r\n", 500)
    handle(post("rg -n def", text), tmp_path)
    saved = next((tmp_path / "outputs" / "s1").iterdir())
    assert saved.read_bytes() == text.encode("utf-8")


def test_partial_coverage_is_stated(tmp_path):
    prompt(tmp_path, "service.py 함수")
    result = handle(
        post("rg -n def src", def_search_output()), tmp_path, min_bytes=4000, budget_bytes=1000
    )
    assert "Only " in result["reason"] and " of 21 lines matching" in result["reason"]


def test_terms_cover_korean_particles_and_paths():
    found = terms("로그인을 고쳐줘", "rg -n refreshToken src/auth/session.py")
    assert {"로그인", "refreshtoken", "session"} <= found


def run_hook(state, payload, mode="observe"):
    return subprocess.run(
        [sys.executable, str(Path(__file__).with_name("tool_hooks.py")), "--state-dir", str(state),
         "--mode", mode],
        input=json.dumps(payload).encode(),
        capture_output=True,
    )  # fmt: skip


def test_settings_file_overrides_mode_without_changing_the_command(tmp_path):
    payload = post("rg -n def", big_output(marker_line=100, lines=400))  # about 12KB
    assert run_hook(tmp_path, payload).stdout == b""
    (tmp_path / "settings.json").write_text(
        json.dumps({"mode": "filter", "min_bytes": 8000, "budget_bytes": 3000}), encoding="utf-8"
    )
    assert json.loads(run_hook(tmp_path, payload).stdout)["decision"] == "block"
    (tmp_path / "settings.json").write_text(json.dumps({"mode": "delete-everything"}))
    assert run_hook(tmp_path, payload).stdout == b""
    assert "invalid settings" in (tmp_path / "hook-log.jsonl").read_text(encoding="utf-8")


def test_module_entry_point_never_fails(tmp_path):
    run = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("tool_hooks.py")), "--state-dir", str(tmp_path),
         "--mode", "filter"],
        input=b"not json",
        capture_output=True,
    )  # fmt: skip
    assert run.returncode == 0 and run.stdout == b""
    assert "error" in (tmp_path / "hook-log.jsonl").read_text(encoding="utf-8")
    payload = json.dumps(post("rg -n def", big_output())).encode()
    run = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("tool_hooks.py")), "--state-dir", str(tmp_path),
         "--mode", "filter"],
        input=payload,
        capture_output=True,
    )  # fmt: skip
    assert json.loads(run.stdout)["decision"] == "block"
