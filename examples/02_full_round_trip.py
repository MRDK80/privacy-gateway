"""Полный privacy round-trip публичного library API Privacy Gateway.

Пример выполняет локальный цикл detect -> policy check -> prepare ->
fake provider -> validate -> restore -> discard на синтетическом письме.
Сетевых вызовов нет: внешний обработчик заменён детерминированной чистой
функцией ``fake_provider``.

Граница доверия:

- обработчик получает единственный аргумент — защищённый текст
  ``prepared.text``;
- контекст восстановления, manifest, route, ключевой материал и путь
  рабочего каталога через границу обработчика не передаются;
- все значения письма синтетические и разрешены политикой репозитория.

Стадии detect, policy check и validate отдельных публичных методов не имеют.
Детекция, проверка безопасности результата и блокирующие правила
выполняются внутри ``prepare``: при срабатывании fail-closed защищённый
текст не возвращается, поднимается ``DetectionError``. Валидация ответа
обработчика выполняется внутри ``restore`` в строгом режиме: неизвестный
или искажённый токен приводит к ``StrictTokenError``, нарушение целостности
артефактов — к ``IntegrityError``. Сценарии блокировки секрета и
недопустимых токенов ведутся отдельными задачами epic.

Номер договора остаётся в защищённом тексте видимым: тип ``DOCUMENT``
детекторами текущей реализации не выделяется. Это документированное
ограничение детектора, а не защищённая сущность.

Запуск из корня репозитория:

    python examples/02_full_round_trip.py

Требуется активный ключ в системном keyring: ``pgw key create``.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from privacy_gateway import GatewayConfig, PreparedPayload, PrivacyGateway

ENTITIES_CONFIG = Path("config.example") / "entities.yaml"

# Словарные значения взяты из config.example/entities.yaml, сетевые и
# контактные — из диапазонов, зарезервированных для документации.
SYNTHETIC_ROLE = "Технический директор"
SYNTHETIC_PERSON = "Синтетический Иванов"
SYNTHETIC_ORG = "ООО Северный Маяк"
SYNTHETIC_PROJECT = "Проект-Орион"
SYNTHETIC_ENVIRONMENT = "staging-test"
SYNTHETIC_DATE = "2026-03-15"
SYNTHETIC_AMOUNT = "150 000 руб."
SYNTHETIC_HOST = "192.0.2.10"
SYNTHETIC_ENDPOINT = "https://example.com/status"
SYNTHETIC_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTHETIC_PHONE = "+7 900 000-00-00"

# Тип DOCUMENT детектором не выделяется: демонстрация ограничения.
UNSUPPORTED_DOCUMENT = "ДГ-А-14"

SUPPORTED_VALUES = (
    SYNTHETIC_ROLE,
    SYNTHETIC_PERSON,
    SYNTHETIC_ORG,
    SYNTHETIC_PROJECT,
    SYNTHETIC_ENVIRONMENT,
    SYNTHETIC_DATE,
    SYNTHETIC_AMOUNT,
    SYNTHETIC_HOST,
    SYNTHETIC_ENDPOINT,
    SYNTHETIC_EMAIL,
    SYNTHETIC_PHONE,
)

# Пунктуация после токена: независимая проверка считает словом всё
# до пробела, запятой или точки, поэтому двоеточие или кавычка сразу
# за длинным токеном даёт слово вида "[ENVIRONMENT_1]:" и fail-closed на
# высокой энтропии. Здесь после токенов идёт пробел, запятая или точка.
SYNTHETIC_LETTER = (
    f"{SYNTHETIC_ROLE} {SYNTHETIC_PERSON}, {SYNTHETIC_ORG}.\n"
    f"По договору № {UNSUPPORTED_DOCUMENT} приняты работы "
    f"по проекту {SYNTHETIC_PROJECT}.\n"
    f"Дата приёмки {SYNTHETIC_DATE}, сумма {SYNTHETIC_AMOUNT}\n"
    f"Контур {SYNTHETIC_ENVIRONMENT}, сервер {SYNTHETIC_HOST}.\n"
    f"Проверка {SYNTHETIC_ENDPOINT}\n"
    f"Ответ направьте на {SYNTHETIC_EMAIL} либо {SYNTHETIC_PHONE}\n"
)

PROVIDER_HEADER = "Черновик ответа обработчика (локально, без сети):\n"
PROVIDER_FOOTER = "Итог: обращение принято к обработке.\n"


def fake_provider(protected_text: str) -> str:
    """Вернуть детерминированный ответ на защищённый текст.

    Чистая функция без сетевых вызовов и состояния. Единственный аргумент —
    защищённый текст; контекст, конфигурация, manifest и путь рабочего
    каталога через эту границу не передаются.
    """
    return PROVIDER_HEADER + protected_text + PROVIDER_FOOTER


def _protected_is_leak_free(prepared: PreparedPayload) -> bool:
    """Проверить отсутствие исходных поддерживаемых значений в тексте."""
    return all(value not in prepared.text for value in SUPPORTED_VALUES)


def _print_stage(title: str, body: str) -> None:
    """Напечатать раздел примера с завершающим переводом строки."""
    print(f"--- {title} ---")
    print(body, end="" if body.endswith("\n") else "\n")


def run_round_trip(provider: Callable[[str], str]) -> int:
    """Выполнить полный round-trip и напечатать проверяемые инварианты.

    Аргумент ``provider`` существует для воспроизводимой проверки границы
    доверия и путей очистки; публичный API библиотеки он не расширяет.
    """
    if not ENTITIES_CONFIG.is_file():
        print("Запустите пример из корня репозитория privacy-gateway.")
        return 2

    base_dir = Path(tempfile.mkdtemp(prefix="pgw-round-trip-"))
    gateway = PrivacyGateway(
        GatewayConfig(
            entities_config_path=ENTITIES_CONFIG,
            workspace_dir=base_dir,
            strict=True,
        )
    )

    prepared = gateway.prepare(SYNTHETIC_LETTER, correlation_id="round-trip")
    try:
        leak_free = _protected_is_leak_free(prepared)
        document_visible = UNSUPPORTED_DOCUMENT in prepared.text
        response = provider(prepared.text)
        restored = gateway.restore(response, context=prepared.context)
    finally:
        gateway.discard(prepared.context)

    workspace_clean = not any(base_dir.iterdir())
    base_dir.rmdir()

    expected = PROVIDER_HEADER + SYNTHETIC_LETTER + PROVIDER_FOOTER
    _print_stage("1. Исходный синтетический документ", SYNTHETIC_LETTER)
    _print_stage("2. Защищённый текст для обработчика", prepared.text)
    _print_stage("3. Ответ обработчика", response)
    _print_stage("4. Восстановленный ответ", restored.text)
    print("--- 5. Публичная статистика и инварианты ---")
    print(f"tokens_prepared={prepared.token_count}")
    print(f"tokens_restored={restored.tokens_restored}")
    print(f"tokens_missing={restored.tokens_missing}")
    print(f"protected_leak_free={leak_free}")
    print(f"unsupported_document_visible={document_visible}")
    print(f"roundtrip_exact={restored.text == expected}")
    print(f"workspace_clean={workspace_clean}")
    return 0


def main() -> int:
    """Запустить пример с детерминированным локальным обработчиком."""
    return run_round_trip(fake_provider)


if __name__ == "__main__":
    raise SystemExit(main())
