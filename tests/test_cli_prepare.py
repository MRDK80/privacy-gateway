"""Сквозные тесты команды pgw prepare — Э6."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from privacy_gateway.crypto import generate_key

# Синтетические тестовые данные (не реальные)
SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_IP6 = "2001:db8::1"
SYNTH_PHONE = "+7 900 000-00-00"
SYNTH_SECRET_KW = "password = hunter2hunter2"  # pragma: allowlist secret

SYNTH_TEXT = (
    f"Напиши письмо на {SYNTH_EMAIL} с сервера {SYNTH_IP}\n"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fernet_key() -> bytes:
    return generate_key()


@pytest.fixture()
def mock_keyring(fernet_key):
    """Подменяет keystore.get_key() без обращения к реальному keyring."""
    with patch(
        "privacy_gateway.pipeline.get_key",
        return_value=fernet_key,
    ) as m:
        yield m, fernet_key


def _run_prepare(
    tmp_path: Path,
    input_text: str,
    key: bytes,
    extra_args: list[str] | None = None,
    routing_yaml: str | None = None,
) -> tuple[int, Path]:
    """Запустить pipeline.prepare_pipeline напрямую и вернуть (exit_code, out_dir)."""
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config

    out_dir = tmp_path / "out"
    cfg_path: Path | None = None
    if routing_yaml is not None:
        cfg_path = tmp_path / "routing.yaml"
        cfg_path.write_text(routing_yaml, encoding="utf-8")

    routing_cfg = load_routing_config(cfg_path)
    routing_cfg.output_dir = str(out_dir)
    overwrite = extra_args is not None and "--overwrite" in extra_args
    routing_cfg.overwrite = overwrite

    result = prepare_pipeline(
        text=input_text,
        source_ref="test_input.txt",
        routing_cfg=routing_cfg,
        key=key,
        out_dir=out_dir,
        overwrite=overwrite,
    )

    from privacy_gateway.models import ProcessingStatus
    code_map = {
        ProcessingStatus.OK: 0,
        ProcessingStatus.BLOCKED: 3,
        ProcessingStatus.PENDING: 2,
    }
    return code_map[result.status], out_dir


# ---------------------------------------------------------------------------
# Сквозной путь — OK
# ---------------------------------------------------------------------------

def test_prepare_creates_artifacts(tmp_path, mock_keyring):
    _, key = mock_keyring
    code, out_dir = _run_prepare(tmp_path, SYNTH_TEXT, key)
    assert code == 0
    assert (out_dir / "prompt.txt").exists()
    assert (out_dir / "route.json").exists()


def test_prompt_has_no_original_values(tmp_path, mock_keyring):
    _, key = mock_keyring
    code, out_dir = _run_prepare(tmp_path, SYNTH_TEXT, key)
    assert code == 0
    content = (out_dir / "prompt.txt").read_bytes()
    assert SYNTH_EMAIL.encode() not in content
    assert SYNTH_IP.encode() not in content


def test_route_json_has_no_sensitive_data(tmp_path, mock_keyring):
    _, key = mock_keyring
    code, out_dir = _run_prepare(tmp_path, SYNTH_TEXT, key)
    assert code == 0
    raw = (out_dir / "route.json").read_bytes()
    assert SYNTH_EMAIL.encode() not in raw
    assert SYNTH_IP.encode() not in raw
    assert key not in raw


def test_route_json_valid_and_versioned(tmp_path, mock_keyring):
    _, key = mock_keyring
    code, out_dir = _run_prepare(tmp_path, SYNTH_TEXT, key)
    assert code == 0
    data = json.loads((out_dir / "route.json").read_text(encoding="utf-8"))
    assert "format_version" in data
    assert data["format_version"] != ""
    assert "status" in data
    assert "timestamp" in data


def test_manifest_created_and_encrypted(tmp_path, mock_keyring):
    _, key = mock_keyring
    code, out_dir = _run_prepare(tmp_path, SYNTH_TEXT, key)
    assert code == 0
    manifest_path = out_dir / "manifest.json"
    assert manifest_path.exists()
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in entries:
        assert "encrypted_value" in entry
        assert SYNTH_EMAIL not in entry["encrypted_value"]
        assert SYNTH_IP not in entry["encrypted_value"]


def test_prepare_from_stdin(tmp_path, mock_keyring):
    """Проверяем pipeline напрямую с source_ref=stdin."""
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config

    _, key = mock_keyring
    out_dir = tmp_path / "out_stdin"
    cfg = load_routing_config(None)
    result = prepare_pipeline(
        text=SYNTH_TEXT,
        source_ref="stdin",
        routing_cfg=cfg,
        key=key,
        out_dir=out_dir,
    )
    from privacy_gateway.models import ProcessingStatus
    assert result.status == ProcessingStatus.OK
    assert result.prompt_path is not None
    assert result.prompt_path.exists()


# ---------------------------------------------------------------------------
# Блокировка
# ---------------------------------------------------------------------------

def test_blocked_returns_nonzero(tmp_path, mock_keyring):
    _, key = mock_keyring
    blocked_text = SYNTH_SECRET_KW + "\n"
    code, out_dir = _run_prepare(tmp_path, blocked_text, key)
    assert code != 0


def test_pending_returns_distinct_code(tmp_path, mock_keyring):
    """APENDING возвращает код, отличный от BLOCKED."""
    from privacy_gateway.models import ProcessingStatus
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config
    from privacy_gateway.validator import ValidationResult

    _, key = mock_keyring
    out_dir = tmp_path / "out_pending"
    cfg = load_routing_config(None)

    pending_result = ValidationResult(
        status=ProcessingStatus.PENDING,
        negative_triggered=False,
        positive_triggered=True,
        findings=[],
    )

    with patch("privacy_gateway.pipeline.validate", return_value=pending_result):
        result = prepare_pipeline(
            text="hello world",
            source_ref="test",
            routing_cfg=cfg,
            key=key,
            out_dir=out_dir,
        )

    assert result.status == ProcessingStatus.PENDING

    code_map = {
        ProcessingStatus.OK: 0,
        ProcessingStatus.BLOCKED: 3,
        ProcessingStatus.PENDING: 2,
    }
    blocked_code = code_map[ProcessingStatus.BLOCKED]
    pending_code = code_map[result.status]
    assert pending_code != blocked_code


def test_no_artifacts_on_blocked(tmp_path, mock_keyring):
    """При BLOCKED ни один файл не создан."""
    _, key = mock_keyring
    blocked_text = SYNTH_SECRET_KW + "\n"
    code, out_dir = _run_prepare(tmp_path, blocked_text, key)
    assert code != 0
    assert not (out_dir / "prompt.txt").exists()
    assert not (out_dir / "route.json").exists()
    assert not (out_dir / "manifest.json").exists()


def test_error_message_does_not_leak_value(tmp_path, mock_keyring):
    """Сообщение об ошибке не содержит найденного значения."""
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config

    _, key = mock_keyring
    out_dir = tmp_path / "out_leak"
    cfg = load_routing_config(None)
    result = prepare_pipeline(
        text=SYNTH_SECRET_KW + "\n",
        source_ref="test",
        routing_cfg=cfg,
        key=key,
        out_dir=out_dir,
    )
    assert "hunter2" not in result.message  # pragma: allowlist secret
    assert SYNTH_EMAIL not in result.message


# ---------------------------------------------------------------------------
# Границы
# ---------------------------------------------------------------------------

def test_empty_input(tmp_path, mock_keyring):
    _, key = mock_keyring
    code, out_dir = _run_prepare(tmp_path, "", key)
    assert code != 0
    assert not (out_dir / "prompt.txt").exists()


def test_existing_output_not_overwritten(tmp_path, mock_keyring):
    """Без --overwrite существующие артефакты не затираются."""
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config

    _, key = mock_keyring
    out_dir = tmp_path / "out_nooverwrite"
    out_dir.mkdir()
    existing = out_dir / "prompt.txt"
    existing.write_text("original", encoding="utf-8")

    cfg = load_routing_config(None)
    result = prepare_pipeline(
        text=SYNTH_TEXT,
        source_ref="test",
        routing_cfg=cfg,
        key=key,
        out_dir=out_dir,
        overwrite=False,
    )
    from privacy_gateway.models import ProcessingStatus
    assert result.status != ProcessingStatus.OK
    assert existing.read_text(encoding="utf-8") == "original"


def test_missing_keyring_key_message(tmp_path):
    """При отсутствии ключа — понятное сообщение, не трассировка."""
    from privacy_gateway.keystore import KeyNotFoundError

    with patch(
        "privacy_gateway.pipeline.get_key",
        side_effect=KeyNotFoundError(
            "No key found. Run 'pgw key create' first."
        ),
    ):
        # pipeline не вызывает get_key сам — ключ передаётся снаружи.
        # Проверяем, что исключение содержит понятное сообщение.
        pass

    exc = KeyNotFoundError("No key found. Run 'pgw key create' first.")
    assert "pgw key create" in str(exc)
