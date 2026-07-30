"""Тесты слоя манифеста (Э4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from privacy_gateway.crypto import DecryptionError, generate_key
from privacy_gateway.manifest import (
    build_manifest,
    decrypt_manifest_entry,
    load_manifest,
    save_manifest,
)
from privacy_gateway.models import EntityType, TokenRecord

_SYNTHETIC_EMAIL = "user@example.com"  # pragma: allowlist secret
_SYNTHETIC_IP = "192.0.2.10"  # pragma: allowlist secret
_SYNTHETIC_PHONE = "+7 900 000-00-00"  # pragma: allowlist secret


@pytest.fixture()
def sample_records() -> list[TokenRecord]:
    return [
        TokenRecord(
            token="[EMAIL_1]",
            entity_type=EntityType.EMAIL,
            fingerprint="fp_email",
        ),
        TokenRecord(
            token="[HOST_1]",
            entity_type=EntityType.HOST,
            fingerprint="fp_ip",
        ),
        TokenRecord(
            token="[PHONE_1]",
            entity_type=EntityType.PHONE,
            fingerprint="fp_phone",
        ),
    ]


@pytest.fixture()
def sample_values() -> list[str]:
    return [_SYNTHETIC_EMAIL, _SYNTHETIC_IP, _SYNTHETIC_PHONE]


def test_manifest_roundtrip(
    sample_records: list[TokenRecord],
    sample_values: list[str],
) -> None:
    key = generate_key()
    entries = build_manifest(sample_records, sample_values, key)
    assert len(entries) == 3
    for entry, original in zip(entries, sample_values):
        recovered = decrypt_manifest_entry(entry, key)
        assert recovered == original


def test_manifest_file_has_no_plaintext(
    sample_records: list[TokenRecord],
    sample_values: list[str],
    tmp_path: Path,
) -> None:
    key = generate_key()
    entries = build_manifest(sample_records, sample_values, key)
    manifest_path = tmp_path / "manifest.json"
    save_manifest(entries, manifest_path)
    raw_bytes = manifest_path.read_bytes()
    for value in sample_values:
        assert value.encode("utf-8") not in raw_bytes, (
            f"Plaintext value {value!r} found in manifest file!"
        )


def test_manifest_wrong_key(
    sample_records: list[TokenRecord],
    sample_values: list[str],
    tmp_path: Path,
) -> None:
    key1 = generate_key()
    key2 = generate_key()
    entries = build_manifest(sample_records, sample_values, key1)
    manifest_path = tmp_path / "manifest.json"
    save_manifest(entries, manifest_path)
    with pytest.raises(DecryptionError):
        load_manifest(manifest_path, key2)


def test_manifest_json_valid(
    sample_records: list[TokenRecord],
    sample_values: list[str],
    tmp_path: Path,
) -> None:
    key = generate_key()
    entries = build_manifest(sample_records, sample_values, key)
    manifest_path = tmp_path / "manifest.json"
    save_manifest(entries, manifest_path)
    raw = manifest_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert isinstance(parsed, list)
    assert len(parsed) == 3
    for item in parsed:
        assert "token" in item
        assert "encrypted_value" in item


def test_manifest_save_load_roundtrip(
    sample_records: list[TokenRecord],
    sample_values: list[str],
    tmp_path: Path,
) -> None:
    key = generate_key()
    entries = build_manifest(sample_records, sample_values, key)
    manifest_path = tmp_path / "manifest.json"
    save_manifest(entries, manifest_path)
    loaded = load_manifest(manifest_path, key)
    assert len(loaded) == len(entries)
    for original, loaded_entry in zip(entries, loaded):
        assert original.token == loaded_entry.token
        assert original.encrypted_value == loaded_entry.encrypted_value
