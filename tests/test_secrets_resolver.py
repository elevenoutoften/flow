from __future__ import annotations

import os
import sys

import pytest

from flow_app.secrets_resolver import (
    SecretResolutionError,
    is_secret_reference,
    redact_secret,
    resolve_secret,
    resolve_secrets,
)


def test_env_secret_resolution(monkeypatch):
    monkeypatch.setenv("FLOW_TEST_VAR", "secret-value")

    assert is_secret_reference("env:FLOW_TEST_VAR")
    assert resolve_secret("env:FLOW_TEST_VAR") == "secret-value"


def test_missing_env_secret_raises(monkeypatch):
    monkeypatch.delenv("FLOW_TEST_VAR", raising=False)

    with pytest.raises(SecretResolutionError):
        resolve_secret("env:FLOW_TEST_VAR")


def test_file_secret_resolution(tmp_path, monkeypatch):
    secret_file = tmp_path / "secret"
    secret_file.write_text(" file-secret \n", encoding="utf-8")
    monkeypatch.setenv("FLOW_SECRET_FILE_ROOTS", str(tmp_path))

    assert is_secret_reference(f"file:{secret_file}")
    assert resolve_secret(f"file:{secret_file}") == "file-secret"


def test_missing_file_secret_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_SECRET_FILE_ROOTS", str(tmp_path))

    with pytest.raises(SecretResolutionError):
        resolve_secret(f"file:{tmp_path / 'missing'}")


def test_file_secret_outside_allowed_roots_is_rejected(tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside"
    allowed_root.mkdir()
    outside_root.mkdir()
    secret_file = outside_root / "secret"
    secret_file.write_text("secret", encoding="utf-8")
    monkeypatch.setenv("FLOW_SECRET_FILE_ROOTS", str(allowed_root))

    with pytest.raises(SecretResolutionError):
        resolve_secret(f"file:{secret_file}")


def test_file_secret_path_outside_allowed_roots_is_rejected(tmp_path, monkeypatch):
    """Resolved paths outside allowed roots are rejected without requiring symlinks."""
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside"
    allowed_root.mkdir()
    outside_root.mkdir()
    outside_secret = outside_root / "secret"
    outside_secret.write_text("secret", encoding="utf-8")

    monkeypatch.setenv("FLOW_SECRET_FILE_ROOTS", str(allowed_root))

    with pytest.raises(SecretResolutionError, match="outside allowed"):
        resolve_secret(f"file:{outside_secret}")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Symlink creation requires elevated privileges on Windows",
)
def test_file_secret_symlink_escape_is_rejected(tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside"
    allowed_root.mkdir()
    outside_root.mkdir()
    outside_secret = outside_root / "secret"
    outside_secret.write_text("secret", encoding="utf-8")
    symlink = allowed_root / "secret-link"
    symlink.symlink_to(outside_secret)
    monkeypatch.setenv("FLOW_SECRET_FILE_ROOTS", str(allowed_root))

    with pytest.raises(SecretResolutionError):
        resolve_secret(f"file:{symlink}")


def test_raw_literal_passthrough_and_redaction():
    assert not is_secret_reference("plain-string")
    assert resolve_secret("plain-string") == "plain-string"
    assert redact_secret("plain-string") == "***"
    assert redact_secret("") == ""
    assert redact_secret("env:FLOW_TEST_VAR") == "env:FLOW_TEST_VAR"


def test_batch_resolution(tmp_path, monkeypatch):
    secret_file = tmp_path / "secret"
    secret_file.write_text("file-secret", encoding="utf-8")
    monkeypatch.setenv("FLOW_TEST_VAR", "env-secret")
    monkeypatch.setenv("FLOW_SECRET_FILE_ROOTS", os.fspath(tmp_path))

    assert resolve_secrets(
        {
            "env_value": "env:FLOW_TEST_VAR",
            "file_value": f"file:{secret_file}",
            "literal_value": "literal",
        }
    ) == {
        "env_value": "env-secret",
        "file_value": "file-secret",
        "literal_value": "literal",
    }
