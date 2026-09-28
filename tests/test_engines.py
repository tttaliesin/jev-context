import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jev_context.common import DomainError
from jev_context.engines import Laya, OpenJev, convert, fingerprint


def profile(**extra):
    return dict(
        family="openjev",
        model_revision="openjev-0.1",
        implementation_revision="e04794ab36e4f7e6040c2547baecdb2737ce2e79",
        precision="fixture",
        template_revision="ko-v1",
        sampling={"samples": 1},
        score_definition="1-H(p)/ln(K); not accuracy or authorization",
        **extra,
    )


def questions():
    return [
        dict(
            id="q1",
            purpose="relevance",
            instructions="관련 근거가 충분한가?",
            options={"relevant": "관련", "insufficient_evidence": "근거 부족"},
            language="ko",
        )
    ]


@pytest.mark.parametrize(
    "family,own,unrelated",
    [
        ("openjev", "engines.py", "modal_engine.py"),
        ("laya", "laya_worker.py", "semif_worker.py"),
        ("semif_openvino", "semif_worker.py", "laya_worker.py"),
        ("openjev_modal", "modal_engine.py", "shared_engine.py"),
    ],
)
def test_fingerprint_tracks_only_selected_family_and_shared_code(
    monkeypatch, family, own, unrelated
):
    original = Path.read_bytes
    changed = set()

    def read(path):
        data = original(path)
        return data + b"\n# changed" if path.name in changed else data

    monkeypatch.setattr(Path, "read_bytes", read)
    selected = {**profile(), "family": family}
    before = fingerprint(selected)
    changed.add(unrelated)
    assert fingerprint(selected) == before
    changed.add(own)
    assert fingerprint(selected) != before
    changed.clear()
    changed.add("common.py")
    assert fingerprint(selected) != before


def raw():
    return dict(
        answers=dict(
            q1=dict(
                choice="relevant",
                probabilities=dict(relevant=0.75, insufficient_evidence=0.25),
                confidence=0.1887,
            )
        ),
        usage={"input_tokens": 10},
    )


@pytest.fixture
def local_engine():
    state = {"raw": raw(), "delay": 0, "status": 200, "posts": 0, "received": threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[]}')

        def do_POST(self):
            state["posts"] += 1
            state["received"].set()
            state["payload"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            time.sleep(state["delay"])
            self.send_response(state["status"])
            self.end_headers()
            try:
                self.wfile.write(json.dumps(state["raw"]).encode())
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()
    thread.join()


def request(engine):
    return dict(
        evaluation_id="eval-1",
        profile_fingerprint=engine.fingerprint,
        source_refs=[],
        state="설계만 작성",
        questions=questions(),
        deadline_ms=1000,
    )


def test_openjev_wire_and_score_semantics(local_engine, monkeypatch):
    endpoint, state = local_engine
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    engine = OpenJev(profile(endpoint=endpoint), token_counter=lambda payload: 10)
    engine.prepare()
    answer = engine.evaluate(request(engine))
    assert answer["status"] == "observed"
    assert answer["answers"][0]["raw_confidence"] == 0.1887
    assert "not accuracy" in answer["answers"][0]["score_definition"]
    assert state["payload"]["questions"]["q1"]["criteria"]["insufficient_evidence"] == "근거 부족"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com",
        "http://localhost:8080",
        "http://127.0.0.1:8080/evil",
        "http://127.0.0.1:8080/?key=x",
        "http://user:pass@127.0.0.1:8080",
    ],
)
def test_engine_address_policy(endpoint):
    with pytest.raises(DomainError, match="loopback"):
        OpenJev(profile(endpoint=endpoint))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 2, True])
def test_invalid_model_numbers_rejected(bad):
    value = raw()
    value["answers"]["q1"]["probabilities"]["relevant"] = bad
    with pytest.raises(DomainError):
        convert(value, questions(), "entropy")


def test_question_mismatch_and_distribution_sum():
    with pytest.raises(DomainError):
        convert({"answers": {}}, questions(), "entropy")
    value = raw()
    value["answers"]["q1"]["probabilities"]["relevant"] = 0.1
    with pytest.raises(DomainError):
        convert(value, questions(), "entropy")


def test_timeout_degrades_and_blocks_new_request(local_engine):
    endpoint, state = local_engine
    engine = OpenJev(profile(endpoint=endpoint), lambda payload: 10)
    engine.prepare()
    state["delay"] = 0.2
    with pytest.raises(DomainError):
        engine.evaluate({**request(engine), "deadline_ms": 10})
    assert engine.state == "degraded"
    with pytest.raises(DomainError):
        engine.evaluate(request(engine))
    assert state["received"].wait(timeout=1)
    assert state["posts"] == 1


def test_missing_tokenizer_or_oversized_input_never_sent(local_engine):
    endpoint, state = local_engine
    for counter in (None, lambda payload: 1000000):
        engine = OpenJev(profile(endpoint=endpoint), counter)
        engine.prepare()
        with pytest.raises(DomainError):
            engine.evaluate(request(engine))
    assert state["posts"] == 0


def test_redirect_not_followed(local_engine):
    endpoint, state = local_engine
    state["status"] = 302
    engine = OpenJev(profile(endpoint=endpoint), lambda payload: 10)
    engine.prepare()
    with pytest.raises(DomainError):
        engine.evaluate(request(engine))
    assert state["posts"] == 1


def test_laya_missing_assets_never_downloads(tmp_path):
    engine = Laya(
        profile(model_path=str(tmp_path / "missing"), python="missing", lock_root=str(tmp_path))
    )
    with pytest.raises(DomainError):
        engine.prepare()
    assert engine.process is None


def test_fingerprint_changes_with_language_or_precision():
    base = profile()
    assert fingerprint(base) != fingerprint({**base, "language": "ko"})
    assert fingerprint(base) != fingerprint({**base, "precision": "fp16"})


def test_laya_real_worker_protocol_and_exclusive_model_lock(tmp_path, monkeypatch):
    package = tmp_path / "fake" / "laya"
    package.mkdir(parents=True)
    package.joinpath("__init__.py").write_text(
        """
from types import SimpleNamespace
class Tokenizer:
 mask_token='[MASK]'
 def __call__(self,text,**kwargs):return {'input_ids':list(range(len(text)))}
class Agent:
 device='cpu'
 model=SimpleNamespace(encoder=SimpleNamespace(config=SimpleNamespace(local_rope_theta=160000)))
 cfg={'max_len':10000,'head_max_len':1000}
 tok=Tokenizer()
 def _to_internal(self,q):return {'t':q['type'],'ins':q['instructions'],'crit':q['criteria']}
 def predict(self,state,questions):
  if state=='hang':
   import time;time.sleep(10)
  return {'answers':{k:{'choice':'relevant','probabilities':{'relevant':0.75,'insufficient_evidence':0.25},'confidence':0.1887} for k in questions},'usage':{'input_tokens':10}}
def load(*args,**kwargs):return Agent()
""",
        encoding="utf-8",
    )
    package.joinpath("common.py").write_text(
        "import json\ndef render_options(q):return list(q['crit'].values())\ndef serialize_state(s):return s if isinstance(s,str) else json.dumps(s,ensure_ascii=False)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(package.parent))
    model = tmp_path / "model"
    model.mkdir()
    for name in ("tokenizer", "encoder"):
        (model / name).mkdir()
    for name in ("rl_agent_config.json", "model.safetensors"):
        (model / name).write_text("{}", encoding="utf-8")
    (model / "encoder/config.json").write_text(
        json.dumps({"rope_parameters": {"sliding_attention": {"rope_theta": 160000}}}),
        encoding="utf-8",
    )
    p = {
        **profile(),
        "family": "laya",
        "python": sys.executable,
        "model_path": str(model),
        "lock_root": str(tmp_path / "locks"),
        "device": "cpu",
    }
    first, second = Laya(p), Laya(p)
    try:
        first.prepare()
        assert first.evaluate(request(first))["status"] == "observed"
        with pytest.raises(DomainError) as error:
            second.prepare()
        assert error.value.code == "engine_busy"
        with pytest.raises(DomainError):
            first.evaluate({**request(first), "state": "hang", "deadline_ms": 10})
        assert first.state == "degraded"
        assert first.process is None
        second.prepare()
        assert second.evaluate(request(second))["status"] == "observed"
    finally:
        first.close()
        second.close()


FAKE_SEMIF_MODULES = {
    "numpy/__init__.py": "int64 = 'int64'\ndef array(value, dtype=None):\n    return value\n",
    "transformers/__init__.py": """
class AutoTokenizer:
    @staticmethod
    def from_pretrained(path, **kwargs):
        return Tokenizer()
class Tokenizer:
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True, **kwargs):
        return ''.join(m['content'] for m in messages) + '|'
    def encode(self, text, add_special_tokens=False):
        return [ord(c) for c in text]
    def decode(self, ids):
        return ''.join(chr(i) for i in ids)
""",
    "openvino/__init__.py": """
import hashlib
import json
import os
from pathlib import Path

__version__ = 'fake-openvino-1'
def get_version():
    return __version__
def native_warning(stage):
    if os.environ.get('FAKE_OV_NATIVE_WARNING') == '1':
        os.write(1, ('native warning: ' + stage + '\\n').encode('ascii'))
        if os.name == 'nt':
            import ctypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
            kernel.GetStdHandle.restype = ctypes.c_void_p
            kernel.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                                         ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
            kernel.WriteFile.restype = ctypes.c_int
            raw = ('win32 native warning: ' + stage + '\\n').encode('ascii')
            buffer = ctypes.create_string_buffer(raw)
            written = ctypes.c_ulong()
            handle = kernel.GetStdHandle(ctypes.c_ulong(-11).value)
            if not kernel.WriteFile(handle, buffer, len(raw), ctypes.byref(written), None):
                raise ctypes.WinError(ctypes.get_last_error())
            assert written.value == len(raw)
native_warning('import')
def log(event, **fields):
    path = os.environ.get('FAKE_OV_EVENTS')
    if path:
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({'event': event, **fields}) + '\\n')
def fail_once(stage):
    if os.environ.get('FAKE_CACHE_FAILURE') != stage:
        return False
    marker = Path(os.environ['FAKE_OV_EVENTS']).with_suffix('.failed-once')
    if marker.exists():
        return False
    marker.write_text(stage, encoding='utf-8')
    return True
class Core:
    def __init__(self):
        self.cache = None
    def set_property(self, config):
        self.cache = config.get('CACHE_DIR', self.cache)
    def get_property(self, device, name):
        properties = {
            'FULL_DEVICE_NAME': 'Fake GPU',
            'DEVICE_ARCHITECTURE': 'fake-architecture',
            'DRIVER_VERSION': os.environ.get('FAKE_OV_DRIVER', 'fake-driver-1'),
        }
        if name not in properties:
            raise RuntimeError('Unsupported fake device property')
        return properties[name]
    def compile_model(self, path, device, config):
        import time
        native_warning('compile')
        time.sleep(float(os.environ.get('FAKE_COMPILE_SECONDS', '0')))
        signature = hashlib.sha256(
            Path(path).read_bytes() + Path(path).with_suffix('.bin').read_bytes()
            + json.dumps({'device': device, 'config': config}, sort_keys=True).encode()
        ).hexdigest()
        hit = False
        if self.cache:
            cache = Path(self.cache)
            cache.mkdir(parents=True, exist_ok=True)
            blob = cache / 'fake-compiled.blob'
            hit = blob.exists() and blob.read_text(encoding='utf-8') == signature
            blob.write_text(signature, encoding='utf-8')
        failed = os.environ.get('FAKE_OV_ALWAYS_FAIL') == '1' or (
            self.cache and fail_once('compile')
        )
        log('compile', cache=self.cache, hit=hit, failed=bool(failed))
        if failed:
            raise RuntimeError('Fake compile failure')
        return Compiled(self.cache, hit)
class Compiled:
    def __init__(self, cache, hit):
        self.cache, self.hit = cache, hit
    def get_property(self, name):
        if name != 'LOADED_FROM_CACHE':
            raise RuntimeError('Unsupported fake compiled property')
        return self.hit
    def create_infer_request(self):
        return Request(self.cache)
class Request:
    def __init__(self, cache):
        self.cache, self.calls = cache, 0
    def __del__(self):
        try:
            log('request_released', cache=self.cache)
        except Exception:
            pass
    def infer(self, inputs):
        import time
        native_warning('infer')
        self.calls += 1
        failed = self.cache and self.calls == 1 and fail_once('warmup')
        log('infer', cache=self.cache, failed=bool(failed))
        if failed:
            raise RuntimeError('Fake cached warmup failure')
        time.sleep(float(os.environ.get('FAKE_INFER_SECONDS', '0')))
        ids, last = inputs['input_ids'][0], inputs['last_index'][0]
        assert all(t == 0 for t in ids[last + 1:]), 'padding must follow the last token'
        logits = [0.0] * 70000
        logits[ord('A')] = 3.0  # always prefer the first listed option
        return [[logits]]
""",
}


def test_semif_openvino_worker_protocol_padding_and_length_limit(tmp_path, monkeypatch):
    from jev_context.engines import SemifOpenVINO

    fakes = tmp_path / "fakes"
    for relative, source in FAKE_SEMIF_MODULES.items():
        (fakes / relative).parent.mkdir(parents=True, exist_ok=True)
        (fakes / relative).write_text(source, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(fakes))
    model = tmp_path / "ir"
    model.mkdir()
    for name in ("model.xml", "model.bin", "tokenizer.json"):
        (model / name).write_text("x", encoding="utf-8")
    (model / "static.json").write_text(json.dumps({"length": 2000, "pad_id": 0}), encoding="utf-8")
    p = {
        **profile(),
        "family": "semif_openvino",
        "python": sys.executable,
        "model_path": str(model),
        "lock_root": str(tmp_path / "locks"),
        "device": "GPU",
    }
    engine = SemifOpenVINO(p)
    try:
        engine.prepare()
        options = {"relevant": "관련", "irrelevant": "무관", "insufficient_evidence": "근거 부족"}
        ask = [dict(id="q1", purpose="relevance", instructions="관련 있는가?",
                    options=options, language="ko")]  # fmt: skip
        result = engine.evaluate({**request(engine), "questions": ask})
        answer = result["answers"][0]
        # A is the first option in the given order; alphabetical order would make it insufficient_evidence.
        assert result["status"] == "observed" and answer["choice"] == "relevant"
        assert set(answer["raw_distribution"]) == set(options)
        assert 0 < answer["raw_confidence"] < 1 and result["usage"]["input_tokens"] > 0
        with pytest.raises(DomainError) as error:  # longer than the fixed graph: rejected
            engine.evaluate({**request(engine), "questions": ask, "state": "긴 원문 " * 400})
        assert error.value.code == "input_incomplete" and engine.state == "shadow"
    finally:
        engine.close()


def semif_fixture(tmp_path, monkeypatch, model_ok=True):
    fakes = tmp_path / "fakes"
    for relative, source in FAKE_SEMIF_MODULES.items():
        (fakes / relative).parent.mkdir(parents=True, exist_ok=True)
        (fakes / relative).write_text(source, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(fakes))
    model = tmp_path / "ir"
    model.mkdir()
    if model_ok:
        for name in ("model.xml", "model.bin", "tokenizer.json"):
            (model / name).write_text("x", encoding="utf-8")
        (model / "static.json").write_text('{"length": 2000, "pad_id": 0}', encoding="utf-8")
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(
        json.dumps(
            {
                **profile(),
                "family": "semif_openvino",
                "python": sys.executable,
                "model_path": str(model),
                "lock_root": str(tmp_path / "locks"),
                "device": "GPU",
                "prepare_timeout_seconds": 20,
            }
        ),  # fmt: skip
        encoding="utf-8",
    )
    from types import SimpleNamespace

    return SimpleNamespace(engine={"state": "shadow", "profile_file": str(profile_file)})


def test_semif_skips_questions_that_cannot_finish_and_keeps_the_worker(tmp_path, monkeypatch):
    # V1 Desktop demo: the second candidate got 0.34 s for two ~0.9 s questions, timed out, and
    # the timeout stopped the worker, so every later judgment in the session abstained.
    from jev_context.engines import SemifOpenVINO

    monkeypatch.setenv("FAKE_INFER_SECONDS", "0.3")
    config = semif_fixture(tmp_path, monkeypatch)
    p = json.loads(open(config.engine["profile_file"], encoding="utf-8").read())
    engine = SemifOpenVINO({**p, "warmup": True})
    try:
        engine.prepare()
        assert 0.3 <= engine.question_seconds < 2
        two = [request(engine)["questions"][0], {**request(engine)["questions"][0], "id": "q2"}]
        with pytest.raises(DomainError) as error:
            engine.evaluate({**request(engine), "questions": two, "deadline_ms": 400})
        assert error.value.code == "judgment_deadline"
        assert engine.state == "shadow" and engine.process.poll() is None
        result = engine.evaluate({**request(engine), "questions": two, "deadline_ms": 2000})
        assert result["status"] == "observed" and len(result["answers"]) == 2
        # The request deadline, not a fixed 2 s cap, bounds a request: 8 questions take ~2.4 s.
        eight = [{**two[0], "id": f"q{i}"} for i in range(8)]
        result = engine.evaluate({**request(engine), "questions": eight, "deadline_ms": 6000})
        assert result["status"] == "observed" and len(result["answers"]) == 8
    finally:
        engine.close()


def test_semif_startup_lock_contention_is_retryable_busy(tmp_path, monkeypatch):
    # V1 Desktop demo: two MCP servers started 11 s apart; the second hit the host startup lock,
    # got plain "busy", was not retried and stayed unavailable for the whole session.
    from jev_context.engines import SemifOpenVINO
    from jev_context.storage import FileLock

    config = semif_fixture(tmp_path, monkeypatch)
    p = json.loads(open(config.engine["profile_file"], encoding="utf-8").read())
    engine = SemifOpenVINO(p)
    with FileLock(Path(p["lock_root"]) / "resident.startup.lock"):
        with pytest.raises(DomainError) as error:
            engine.prepare()
    assert error.value.code == "engine_busy"
    try:
        engine.prepare()  # free again: the retry succeeds
        assert engine.state == "shadow"
        events = [json.loads(line)["event"] for line in
                  (Path(p["lock_root"]) / "engine.log").read_text(encoding="utf-8").splitlines()]  # fmt: skip
        assert events[-2:] == ["prepare_start", "ready"] and "prepare_failed" in events
    finally:
        engine.close()


@pytest.mark.parametrize("background", [False, True])
def test_enabling_local_judgments_never_starts_weights_without_a_request(
    tmp_path, monkeypatch, background
):
    from jev_context.cli import prepare_engine
    from jev_context.shared_engine import SharedLocalEngine

    config = semif_fixture(tmp_path, monkeypatch, model_ok=False)
    engine = prepare_engine(config, background=background)
    assert isinstance(engine, SharedLocalEngine)
    for _ in range(3):
        assert engine.state == "idle"
    assert not engine.directory.exists()
    assert "preparation_error" not in config.engine
    engine.close()
    assert engine.state == "disabled"


def cache_fixture(tmp_path, monkeypatch):
    config = semif_fixture(tmp_path, monkeypatch)
    settings = json.loads(Path(config.engine["profile_file"]).read_text(encoding="utf-8"))
    settings.update(cache_dir=str(tmp_path / "compile-cache"), warmup=True)
    trace = tmp_path / "fake-ov.jsonl"
    monkeypatch.setenv("FAKE_OV_EVENTS", str(trace))
    return settings, trace


def compile_events(trace):
    if not trace.exists():
        return []
    return [
        event
        for line in trace.read_text(encoding="utf-8").splitlines()
        if (event := json.loads(line))["event"] == "compile"
    ]


def test_semif_native_stdout_warnings_reach_stderr_without_corrupting_json(tmp_path, monkeypatch):
    from jev_context.engines import SemifOpenVINO

    settings, _ = cache_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_OV_NATIVE_WARNING", "1")
    engine = SemifOpenVINO(settings)
    try:
        engine.prepare()
        assert engine.state == "shadow" and engine.startup is not None
        for _ in range(2):
            result = engine.evaluate(request(engine))
            assert result["status"] == "observed"
            assert result["answers"][0]["choice"] == "relevant"
            assert result["usage"]["input_tokens"] > 0
    finally:
        engine.close()
    stderr = (Path(settings["lock_root"]) / "worker.stderr.log").read_bytes()
    assert b"native warning: import\n" in stderr
    assert b"native warning: compile\n" in stderr
    assert stderr.splitlines().count(b"native warning: infer") >= 4
    if sys.platform == "win32":
        assert b"win32 native warning: import\n" in stderr
        assert b"win32 native warning: compile\n" in stderr
        assert stderr.splitlines().count(b"win32 native warning: infer") >= 4


@pytest.mark.parametrize(
    ("variable", "values"),
    [
        ("OV_GPU_USE_ONEDNN", ("0", "1")),
        ("OV_GPU_FORCE_IMPLEMENTATIONS", ("implementation-a", "implementation-b")),
        ("OV_CACHE_MODE", ("OPTIMIZE_SPEED", "OPTIMIZE_SIZE")),
    ],
)
def test_semif_runtime_environment_separates_cache_and_preserves_matching_reuse(
    tmp_path, monkeypatch, variable, values
):
    from jev_context.engines import SemifOpenVINO

    settings, trace = cache_fixture(tmp_path, monkeypatch)
    for name in list(os.environ):
        if name.startswith("OV_"):
            monkeypatch.delenv(name)
    environments = [None, *values] * 2
    for value in environments:
        if value is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, value)
        engine = SemifOpenVINO(settings)
        try:
            engine.prepare()
            assert engine.startup["runtime_environment"] == (
                {variable: value} if value is not None else {}
            )
            result = engine.evaluate(request(engine))
            assert result["status"] == "observed"
            assert result["answers"][0]["choice"] == "relevant"
        finally:
            engine.close()
    events = compile_events(trace)
    assert len(events) == 6
    assert [event["hit"] for event in events] == [False] * 3 + [True] * 3
    paths = [event["cache"] for event in events]
    assert len(set(paths[:3])) == 3 and paths[:3] == paths[3:]
    for value, path in zip(environments[:3], paths[:3], strict=True):
        identity = json.loads((Path(path) / "identity.json").read_text(encoding="utf-8"))
        assert identity["runtime_environment"] == ({variable: value} if value is not None else {})


def test_semif_managed_cache_survives_worker_exit_and_preserves_judgments(tmp_path, monkeypatch):
    from jev_context.engines import SemifOpenVINO

    settings, trace = cache_fixture(tmp_path, monkeypatch)
    cache_root = Path(settings["cache_dir"])
    original_model = {
        path.name: path.read_bytes() for path in Path(settings["model_path"]).iterdir()
    }
    answers = []
    for _ in range(2):
        engine = SemifOpenVINO(settings)
        try:
            engine.prepare()
            result = engine.evaluate(request(engine))
            assert result["status"] == "observed"
            assert result["answers"][0]["choice"] == "relevant"
            answers.append((result["answers"], result["usage"]))
            with pytest.raises(DomainError) as invalid:
                engine.evaluate({**request(engine), "state": "길이 제한 검증 " * 400})
            assert invalid.value.code == "input_incomplete"
            assert engine.state == "shadow"
        finally:
            engine.close()
        assert engine.process is None
    events = compile_events(trace)
    assert len(events) == 2
    assert [event["hit"] for event in events] == [False, True]
    cache_path = Path(events[0]["cache"])
    assert cache_path != cache_root and cache_path.is_relative_to(cache_root)
    assert events[1]["cache"] == events[0]["cache"]
    assert answers[0] == answers[1]
    assert original_model == {
        path.name: path.read_bytes() for path in Path(settings["model_path"]).iterdir()
    }


@pytest.mark.parametrize("failure", ["compile", "warmup"])
def test_semif_rejects_only_failed_cache_namespace_and_retries_once_with_fresh_cache(
    tmp_path, monkeypatch, failure
):
    from jev_context.engines import SemifOpenVINO

    settings, trace = cache_fixture(tmp_path, monkeypatch)
    cache_root = Path(settings["cache_dir"])
    sibling = cache_root / "unrelated-cache" / "keep.bin"
    sibling.parent.mkdir(parents=True)
    sibling.write_bytes(b"unrelated compiled cache")
    marker = cache_root / "keep.txt"
    marker.write_bytes(b"user file")
    monkeypatch.setenv("FAKE_CACHE_FAILURE", failure)
    engine = SemifOpenVINO(settings)
    try:
        engine.prepare()
        result = engine.evaluate(request(engine))
        assert result["status"] == "observed"
        assert result["answers"][0]["choice"] == "relevant"
        assert engine.question_seconds is not None
    finally:
        engine.close()
    events = compile_events(trace)
    assert len(events) == 2
    rejected = Path(events[0]["cache"])
    assert rejected != cache_root and rejected.is_relative_to(cache_root)
    assert events[1]["cache"] == events[0]["cache"]
    assert [event["hit"] for event in events] == [False, False]
    if failure == "warmup":
        lifecycle = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        failed = next(
            index
            for index, event in enumerate(lifecycle)
            if event["event"] == "infer" and event["failed"]
        )
        retry = next(
            index
            for index in range(failed + 1, len(lifecycle))
            if lifecycle[index]["event"] == "compile"
        )
        assert any(event["event"] == "request_released" for event in lifecycle[failed + 1 : retry])
    quarantined = [
        path
        for path in rejected.parent.iterdir()
        if path != rejected and path.is_dir() and (path / "fake-compiled.blob").exists()
    ]
    assert len(quarantined) == 1
    assert (rejected / "fake-compiled.blob").is_file()
    assert sibling.read_bytes() == b"unrelated compiled cache"
    assert marker.read_bytes() == b"user file"
    restored = SemifOpenVINO(settings)
    try:
        restored.prepare()
        again = restored.evaluate(request(restored))
        assert again["answers"] == result["answers"]
        assert again["usage"] == result["usage"]
    finally:
        restored.close()
    # A repair must repopulate the usable cache, rather than repeat cold fallback forever.
    assert [event["hit"] for event in compile_events(trace)] == [False, False, True]


def test_semif_failing_cache_and_fresh_compile_do_not_loop_or_leave_worker(tmp_path, monkeypatch):
    from jev_context.engines import SemifOpenVINO

    settings, trace = cache_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_OV_ALWAYS_FAIL", "1")
    engine = SemifOpenVINO(settings)
    try:
        with pytest.raises(DomainError) as failed:
            engine.prepare()
        assert failed.value.code == "engine_unavailable"
        assert engine.process is None and engine.state != "shadow"
    finally:
        engine.close()
    events = compile_events(trace)
    assert len(events) == 2 and all(event["failed"] for event in events)
    assert events[0]["cache"] and events[1]["cache"] == events[0]["cache"]


@pytest.mark.parametrize("changed", ["manifest.json", "model.bin"])
def test_semif_existing_cache_does_not_bypass_manifest_or_weight_validation(
    tmp_path, monkeypatch, changed
):
    from jev_context.engines import SemifOpenVINO

    settings, trace = cache_fixture(tmp_path, monkeypatch)
    model = Path(settings["model_path"])
    manifest = model / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "files": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in model.iterdir()
                }
            }
        ),
        encoding="utf-8",
    )
    settings["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    engine = SemifOpenVINO(settings)
    try:
        engine.prepare()
        assert engine.evaluate(request(engine))["status"] == "observed"
    finally:
        engine.close()
    before = compile_events(trace)
    assert len(before) == 1 and before[0]["cache"]
    (model / changed).write_bytes(b"changed after the cache was populated")
    changed_engine = SemifOpenVINO(settings)
    try:
        with pytest.raises(DomainError) as rejected:
            changed_engine.prepare()
        assert rejected.value.code == "engine_unavailable"
        assert changed_engine.process is None
    finally:
        changed_engine.close()
    assert compile_events(trace) == before
