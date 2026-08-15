"""Tests for route.json v1.1 integrity binding — Э7-prep.

Покрывают генерацию manifest_sha256 пиплайном
и все ветви verify_manifest_integrity.

Синтетика: user@example.com, 192.0.2.10, 2001:db8::1, +7 900 000-00-00.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from privacy_gateway.crypto import generate_key
from privacy_gateway.models import ConfigurationError
from privacy_gateway.routing import verify_manifest_integrity

MockKeyring = tuple[MagicMock, bytes]

# ---------------------------------------------------------------------------
# Synthetic test data (not real PII)
# ---------------------------------------------------------------------------
SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_IP6 = "2001:db8::1"
SYNTH_PHONE = "+7 900 000-00-00"
SYNTH_TEXT = f"Письмо на {SYNTH_EMAIL} с сервера {SYNTH_IP}\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def fernet_key() -> bytes:
    return generate_key()


@pytest.fixture()
def mock_keyring(fernet_key: bytes) -> Iterator[MockKeyring]:
    with patch("privacy_gateway.pipeline.get_key", return_value=fernet_key) as m:
        yield m, fernet_key


def _run_prepare(tmp_path: Path, key: bytes, text: str = SYNTH_TEXT) -> Path:
    """Run prepare_pipeline and return out_dir. Asserts OK status."""
    from privacy_gateway.models import ProcessingStatus
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config

    out_dir = tmp_path / "out"
    cfg = load_routing_config(None)
    result = prepare_pipeline(
        text=text,
        source_ref="test_integrity.txt",
        routing_cfg=cfg,
        key=key,
        out_dir=out_dir,
    )
    assert result.status == ProcessingStatus.OK, f"prepare failed: {result.message}"
    return out_dir


# ---------------------------------------------------------------------------
# Generation tests
# ---------------------------------------------------------------------------

def test_route_has_manifest_sha256(tmp_path: Path, mock_keyring: MockKeyring) -> None:
    """After prepare, route.json must contain manifest_sha256 (64 lowercase hex)."""
    _, key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    assert "manifest_sha256" in data
    sha = data["manifest_sha256"]
    assert isinstance(sha, str)
    assert len(sha) == 64
    assert sha == sha.lower()
    assert all(c in "0123456789abcdef" for c in sha)


def test_format_version_is_1_1(tmp_path: Path, mock_keyring: MockKeyring) -> None:
    """format_version must be '1.1' after prepare."""
    _, key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    assert data["format_version"] == "1.1"


def test_sha256_matches_actual_file(tmp_path: Path, mock_keyring: MockKeyring) -> None:
    """manifest_sha256 in route.json must equal sha256 of manifest.json bytes."""
    _, key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    actual = hashlib.sha256((out_dir / "manifest.json").read_bytes()).hexdigest()
    assert data["manifest_sha256"] == actual


# ---------------------------------------------------------------------------
# Verification tests
# ---------------------------------------------------------------------------

def test_verify_passes_on_valid_pair(tmp_path: Path, mock_keyring: MockKeyring) -> None:
    """A freshly prepared pair must pass verify_manifest_integrity silently."""
    _, key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    route_data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    # Must not raise
    verify_manifest_integrity(route_data, out_dir / "manifest.json")


def test_verify_detects_modified_manifest(
    tmp_path: Path, mock_keyring: MockKeyring
) -> None:
    """Appending a byte to manifest.json must cause ConfigurationError."""
    _, key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    route_data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    manifest_path = out_dir / "manifest.json"
    # Modify manifest: append one byte
    with manifest_path.open("ab") as f:
        f.write(b" ")
    with pytest.raises(ConfigurationError, match="integrity check failed"):
        verify_manifest_integrity(route_data, manifest_path)


def test_verify_detects_swapped_manifest(
    tmp_path: Path, mock_keyring: MockKeyring
) -> None:
    """Central test: manifest from a different prepare run must be rejected."""
    _, key = mock_keyring
    out_dir1 = _run_prepare(tmp_path / "run1", key)
    out_dir2 = _run_prepare(tmp_path / "run2", key)

    route_data = json.loads((out_dir1 / "route.json").read_text(encoding="utf-8"))
    # Swap: use manifest from run2 with route from run1
    foreign_manifest = out_dir2 / "manifest.json"
    with pytest.raises(ConfigurationError, match="integrity check failed"):
        verify_manifest_integrity(route_data, foreign_manifest)


def test_verify_missing_field_in_1_1_errors(tmp_path: Path) -> None:
    """version 1.1 route.json without manifest_sha256 must raise ConfigurationError."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"[]")  # pragma: allowlist secret
    route_data = {"format_version": "1.1", "status": "OK"}
    with pytest.raises(ConfigurationError, match="manifest_sha256"):
        verify_manifest_integrity(route_data, manifest_path)


def test_verify_skips_for_1_0(tmp_path: Path) -> None:
    """version 1.0 route.json (no manifest_sha256) must pass without error."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"[]")
    route_data = {"format_version": "1.0", "status": "OK"}
    # Must not raise
    verify_manifest_integrity(route_data, manifest_path)


def test_verify_unknown_version_errors(tmp_path: Path) -> None:
    """Unknown format_version must raise ConfigurationError."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"[]")
    route_data = {"format_version": "2.0", "status": "OK"}
    with pytest.raises(ConfigurationError, match="Unknown route.json format_version"):
        verify_manifest_integrity(route_data, manifest_path)


def test_verify_missing_manifest_file(
    tmp_path: Path, mock_keyring: MockKeyring
) -> None:
    """Missing manifest.json must raise ConfigurationError.

    The error includes path information but no raw traceback.
    """
    _, key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    route_data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    absent_path = tmp_path / "nonexistent" / "manifest.json"
    with pytest.raises(ConfigurationError) as exc_info:
        verify_manifest_integrity(route_data, absent_path)
    msg = str(exc_info.value)
    assert "manifest.json" in msg or str(absent_path) in msg
    # Must not be a raw OSError / FileNotFoundError traceback
    assert "Traceback" not in msg


def test_error_message_has_no_manifest_content(
    tmp_path: Path, mock_keyring: MockKeyring
) -> None:
    """Error message on hash mismatch must not contain manifest file content."""
    _, key = mock_keyring
    out_dir = _run_prepare(tmp_path, key)
    route_data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    manifest_path = out_dir / "manifest.json"
    manifest_content = manifest_path.read_text(encoding="utf-8")
    # Corrupt manifest
    with manifest_path.open("ab") as f:
        f.write(b"X")
    with pytest.raises(ConfigurationError) as exc_info:
        verify_manifest_integrity(route_data, manifest_path)
    msg = str(exc_info.value)
    # The error must not include any fragment of manifest file content
    for token_candidate in manifest_content.split()[:5]:
        if len(token_candidate) > 8:  # skip short JSON tokens like [] {} ,
            assert token_candidate not in msg, (
                f"Error message leaks manifest content fragment: {token_candidate!r}"
            )
