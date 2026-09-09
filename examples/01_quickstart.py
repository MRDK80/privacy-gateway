\
"""Минимальный quickstart публичного library API Privacy Gateway.

Пример выполняет локальный цикл prepare -> локальный ответ -> restore ->
discard на синтетическом тексте. Сетевых вызовов нет: внешний обработчик
заменён детерминированной локальной функцией.

Наружу передаётся только защищённый текст ``prepared.text``. Контекст
восстановления, manifest, ключевой материал и исходный текст внешнему
обработчику не передаются и не печатаются.

Запуск из корня репозитория:

    python examples/01_quickstart.py

Требуется активный ключ в системном keyring: ``pgw key create``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from privacy_gateway import GatewayConfig, PreparedPayload, PrivacyGateway

ENTITIES_CONFIG = Path("config.example") / "entities.yaml"

# Только синтетические значения, разрешённые политикой репозитория.
SYNTHETIC_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTHETIC_HOST = "192.0.2.10"
SYNTHETIC_PHONE = "+7 900 000-00-00"

SYNTHETIC_TEXT = (
    f"Свяжитесь: {SYNTHETIC_EMAIL}, сервер {SYNTHETIC_HOST}, "
    f"телефон {SYNTHETIC_PHONE}.\n"
)

RESPONSE_PREFIX = "Ответ обработчика:\n"


def external_processor(protected_text: str) -> str:
    """Вернуть детерминированный локальный ответ на защищённый текст.

    Заменитель внешнего обработчика получает только защищённый текст и не
    выполняет сетевых вызовов.
    """
    return RESPONSE_PREFIX + protected_text


def _protected_is_leak_free(prepared: PreparedPayload) -> bool:
    """Проверить отсутствие синтетических значений в защищённом тексте."""
    values = (SYNTHETIC_EMAIL, SYNTHETIC_HOST, SYNTHETIC_PHONE)
    return all(value not in prepared.text for value in values)


def main() -> int:
    """Выполнить quickstart и напечатать проверяемые инварианты."""
    if not ENTITIES_CONFIG.is_file():
        print("Запустите пример из корня репозитория privacy-gateway.")
        return 2

    base_dir = Path(tempfile.mkdtemp(prefix="pgw-quickstart-"))
    gateway = PrivacyGateway(
        GatewayConfig(
            entities_config_path=ENTITIES_CONFIG,
            workspace_dir=base_dir,
            strict=True,
        )
    )

    prepared = gateway.prepare(SYNTHETIC_TEXT, correlation_id="quickstart")
    try:
        leak_free = _protected_is_leak_free(prepared)
        response = external_processor(prepared.text)
        restored = gateway.restore(response, context=prepared.context)
    finally:
        gateway.discard(prepared.context)

    workspace_clean = not any(base_dir.iterdir())
    base_dir.rmdir()

    expected = RESPONSE_PREFIX + SYNTHETIC_TEXT
    print(f"tokens_prepared={prepared.token_count}")
    print(f"tokens_restored={restored.tokens_restored}")
    print(f"tokens_missing={restored.tokens_missing}")
    print(f"protected_leak_free={leak_free}")
    print(f"roundtrip_exact={restored.text == expected}")
    print(f"workspace_clean={workspace_clean}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
