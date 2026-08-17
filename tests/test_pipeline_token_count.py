"""Тесты внутреннего контракта PipelineResult.token_count — #32.

Проверяют, что счётчик токенов отдаётся результатом конвейера
и совпадает с полем token_count в route.json.

Синтетика: user@example.com, 192.0.2.10.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from privacy_gateway.crypto import generate_key
from privacy_gateway.models import ProcessingStatus
from privacy_gateway.pipeline import PipelineResult

MockKeyring = tuple[MagicMock, bytes]

# ---------------------------------------------------------------------------
# Synthetic test data (not real PII)
# ---------------------------------------------------------------------------
SYNTH_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTH_IP = "192.0.2.10"
SYNTH_TEXT = f"Письмо на {SYNTH_EMAIL} с сервера {SYNTH_IP}\n"
EXPECTED_TOKENS = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def fernet_key() -> bytes:
    return generate_key()


@pytest.fixture()
def mock_keyring(fernet_key: bytes) -> Iterator[MockKeyring]:
    with patch(
        "privacy_gateway.pipeline.get_key", return_value=fernet_key
    ) as m:
        yield m, fernet_key


def _run_prepare(
    tmp_path: Path, key: bytes, text: str = SYNTH_TEXT
) -> PipelineResult:
    """Запустить prepare_pipeline и вернуть результат. Проверяет статус OK."""
    from privacy_gateway.pipeline import prepare_pipeline
    from privacy_gateway.routing import load_routing_config

    out_dir = tmp_path / "out"
    cfg = load_routing_config(None)
    result = prepare_pipeline(
        text=text,
        source_ref="test_pipeline_token_count.txt",
        routing_cfg=cfg,
        key=key,
        out_dir=out_dir,
    )
    assert result.status == ProcessingStatus.OK, (
        f"prepare failed: {result.message}"
    )
    return result


# ---------------------------------------------------------------------------
# OK-результат отдаёт счётчик
# ---------------------------------------------------------------------------

def test_ok_result_exposes_token_count(
    tmp_path: Path, mock_keyring: MockKeyring
) -> None:
    """Успешный конвейер возвращает число созданных токенов."""
    _, key = mock_keyring
    result = _run_prepare(tmp_path, key)
    assert result.token_count == EXPECTED_TOKENS


def test_route_json_matches_result_token_count(
    tmp_path: Path, mock_keyring: MockKeyring
) -> None:
    """route.json и PipelineResult содержат один и тот же счётчик."""
    _, key = mock_keyring
    result = _run_prepare(tmp_path, key)
    assert result.route_path is not None
    decoded = json.loads(result.route_path.read_text(encoding="utf-8"))
    assert decoded["token_count"] == result.token_count


# ---------------------------------------------------------------------------
# Не-OK результаты и совместимость конструктора
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status",
    [ProcessingStatus.PENDING, ProcessingStatus.BLOCKED],
)
def test_early_results_default_to_zero(status: ProcessingStatus) -> None:
    """Результат без явного счётчика имеет token_count == 0."""
    result = PipelineResult(status=status, message="early exit")
    assert result.token_count == 0


def test_legacy_construction_stays_valid(tmp_path: Path) -> None:
    """Прежнее конструирование без token_count остаётся валидным."""
    result = PipelineResult(
        status=ProcessingStatus.OK,
        message="OK: artifacts created.",
        prompt_path=tmp_path / "prompt.txt",
        route_path=tmp_path / "route.json",
        manifest_path=tmp_path / "manifest.json",
        findings_summary=[],
    )
    assert result.token_count == 0
