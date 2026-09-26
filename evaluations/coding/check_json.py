import pytest

from jev_context.cli import read_call_input


def read(tmp_path, value):
    path = tmp_path / "input.json"
    path.write_bytes(value)
    return read_call_input(path)


@pytest.mark.parametrize(
    "value",
    [
        b'{"x":1,"x":2}',
        b'{"nested":{"a":1,"a":2}}',
        b'{"list":[{"x":1,"x":2}]}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b"[]",
        b'{"x":"\xff"}',
    ],
)
def test_invalid_input_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        read(tmp_path, value)


def test_distinct_nested_objects_can_reuse_keys(tmp_path):
    assert read(tmp_path, b'{"a":{"x":1},"b":{"x":2}}') == {"a": {"x": 1}, "b": {"x": 2}}


def test_exact_byte_boundary_and_overflow(tmp_path):
    assert read(tmp_path, b"{}" + b" " * 524286) == {}
    with pytest.raises(ValueError):
        read(tmp_path, b"{}" + b" " * 524287)


def test_korean_payload(tmp_path):
    assert read(tmp_path, '{"goal":"원문 유지"}'.encode()) == {"goal": "원문 유지"}
