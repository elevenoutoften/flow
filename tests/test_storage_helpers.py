"""Tests for typed storage helpers and malformed data handling."""

from flow_app.storage_helpers import (
    get_active_bool,
    get_bool_field,
    get_comma_list,
    get_json_list,
    set_comma_list,
    set_json_list,
)


def test_get_bool_field_true_values():
    assert get_bool_field(1) is True
    assert get_bool_field(True) is True


def test_get_bool_field_false_values():
    assert get_bool_field(0) is False
    assert get_bool_field(False) is False


def test_get_bool_field_none():
    assert get_bool_field(None) is False


def test_get_active_bool_none_defaults_true():
    assert get_active_bool(None) is True


def test_get_active_bool_false():
    assert get_active_bool(0) is False


def test_get_active_bool_true():
    assert get_active_bool(1) is True


def test_get_comma_list_normal():
    assert get_comma_list("task_created,task_moved,task_completed") == [
        "task_created",
        "task_moved",
        "task_completed",
    ]


def test_get_comma_list_empty():
    assert get_comma_list("") == []
    assert get_comma_list(None) == []


def test_get_comma_list_whitespace():
    assert get_comma_list(" a , b , c ") == ["a", "b", "c"]


def test_get_comma_list_trailing_comma():
    assert get_comma_list("a,b,") == ["a", "b"]


def test_set_comma_list_normalizes_items():
    assert set_comma_list([" a ", "", "b", " "]) == "a,b"


def test_get_json_list_normal():
    assert get_json_list('["file1.py","file2.py"]') == ["file1.py", "file2.py"]


def test_get_json_list_empty():
    assert get_json_list("") == []
    assert get_json_list(None) == []


def test_get_json_list_malformed_returns_empty_and_logs(caplog):
    result = get_json_list("not-json-at-all")
    assert result == []
    assert "Malformed JSON list" in caplog.text


def test_get_json_list_non_list_returns_empty_and_logs(caplog):
    result = get_json_list('{"not":"a-list"}')
    assert result == []
    assert "Non-list JSON value" in caplog.text


def test_set_json_list_serializes_compact_json():
    assert set_json_list(["file1.py", "file2.py"]) == '["file1.py","file2.py"]'
