"""Отрицательный пример: секрет блокирует подготовку до внешнего вызова.

Пример демонстрирует fail-closed поведение публичного API Privacy Gateway.
Синтетический запрос содержит обычную псевдонимизируемую сущность и один
заведомо тестовый секрет. Обнаружение секрета останавливает подготовку
внутри ``prepare``: защищённый текст не формируется, поднимается
``DetectionError`` со статусом ``BLOCKED``, рабочий каталог освобождается,
а обработчик-шпион не получает ни одного вызова.

Граница доверия:

- внешний обработчик представлен шпионом ``SpyProvider``: он считает вызовы
  и при вызове поднимает ``AssertionError``;
- после блокировки обработчику не передаётся ничего — ни защищённый текст,
  ни исходный запрос, ни контекст восстановления, ни путь каталога;
- вывод содержит только безопасные флаги и счётчики. Значение секрета,
  исходный текст, сообщение исключения, manifest и route не печатаются.

Ожидаемая блокировка считается успешным результатом демонстрации, поэтому
процесс завершается кодом ``0``. Код ``1`` означает нарушение инварианта:
блокировки не было либо обработчик получил вызов. Код ``2`` означает запуск
не из корня репозитория.

Запуск из корня репозитория:

python examples/03_blocked_secret.py

Требуется активный ключ в системном keyring: ``pgw key create``. Ключ
читается до детекции, поэтому без него пример завершится ``KeyStoreError``:
это тоже ожидаемое fail-closed поведение.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from privacy_gateway import DetectionError, GatewayConfig, PrivacyGateway

ENTITIES_CONFIG = Path("config.example") / "entities.yaml"

# Только синтетические значения, разрешённые политикой репозитория.
SYNTHETIC_EMAIL = "user@example.com"  # pragma: allowlist secret
SYNTHETIC_PROJECT = "Проект-Орион"

# Заведомо тестовая строка: низкая энтропия, русский маркер, не похожа на
# действующий credential стороннего сервиса. Детектор ловит её правилом
# PASSWORD: ключевое слово, разделитель, значение без пробелов.
SYNTHETIC_SECRET_VALUE = "ПРИМЕР-НЕ-СЕКРЕТ-0000"  # pragma: allowlist secret
SYNTHETIC_SECRET_LINE = f"пароль: {SYNTHETIC_SECRET_VALUE}"  # pragma: allowlist secret

# Часть запроса без секрета: обычная сущность сама по себе блокировку
# не вызывает. Используется как контрольный вход в тестах.
SYNTHETIC_REQUEST_WITHOUT_SECRET = (
    f"Задача по проекту {SYNTHETIC_PROJECT}.\n"
    f"Ответ направьте на {SYNTHETIC_EMAIL}\n"
)

SYNTHETIC_REQUEST = (
    SYNTHETIC_REQUEST_WITHOUT_SECRET + f"Доступ к стенду — {SYNTHETIC_SECRET_LINE}\n"
)

PSEUDONYMIZABLE_VALUES = (SYNTHETIC_EMAIL, SYNTHETIC_PROJECT)


class SpyProvider:
    """Обработчик-шпион, который не должен быть вызван ни разу.

    Считает вызовы и при вызове поднимает ``AssertionError``, поэтому
    нарушение границы доверия невозможно пропустить.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, protected_text: str) -> str:
        """Зафиксировать недопустимый вызов и прервать выполнение."""
        self.calls += 1
        raise AssertionError("Обработчик не должен вызываться после блокировки.")


def _pseudonymizable_entity_present() -> bool:
    """Проверить, что во входе есть обычная псевдонимизируемая сущность."""
    return all(value in SYNTHETIC_REQUEST for value in PSEUDONYMIZABLE_VALUES)


def run_blocked_secret_demo(provider: SpyProvider) -> int:
    """Выполнить сценарий блокировки и напечатать безопасные инварианты.

    Аргумент ``provider`` существует для воспроизводимой проверки границы
    доверия; публичный API библиотеки он не расширяет.
    """
    if not ENTITIES_CONFIG.is_file():
        print("Запустите пример из корня репозитория privacy-gateway.")
        return 2

    base_dir = Path(tempfile.mkdtemp(prefix="pgw-blocked-"))
    gateway = PrivacyGateway(
        GatewayConfig(
            entities_config_path=ENTITIES_CONFIG,
            workspace_dir=base_dir,
            strict=True,
        )
    )

    blocked = False
    failure_class = "none"
    failure_status = "none"
    protected_created = False

    try:
        prepared = gateway.prepare(SYNTHETIC_REQUEST, correlation_id="blocked-secret")
    except DetectionError as exc:
        blocked = True
        failure_class = type(exc).__name__
        failure_status = exc.status or "UNSPECIFIED"
    else:
        # Защитная ветка: защищённый payload появляться не должен, поэтому
        # обработчик не вызывается, а контекст немедленно уничтожается.
        protected_created = True
        gateway.discard(prepared.context)

    workspace_clean = not any(base_dir.iterdir())
    if workspace_clean:
        base_dir.rmdir()

    print(f"secret_blocked={blocked}")
    print(f"failure_class={failure_class}")
    print(f"failure_status={failure_status}")
    print(f"provider_calls={provider.calls}")
    print(f"protected_payload_created={protected_created}")
    print(f"pseudonymizable_entity_present={_pseudonymizable_entity_present()}")
    print(f"workspace_clean={workspace_clean}")

    if blocked and provider.calls == 0 and not protected_created:
        return 0
    return 1


def main() -> int:
    """Запустить пример с обработчиком-шпионом."""
    return run_blocked_secret_demo(SpyProvider())


if __name__ == "__main__":
    raise SystemExit(main())
