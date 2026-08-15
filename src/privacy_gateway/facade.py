"""Библиотечный API Privacy Gateway — публичные модели контракта.

Модуль задаёт стабильные типы, которыми внешнее приложение обменивается
с Privacy Gateway. Класс ``PrivacyGateway`` с методами ``prepare`` и
``restore`` добавляется в этот же модуль следующими шагами задачи; здесь
зафиксирован только контракт данных.

Жизненный цикл контекста восстановления:

- Контекст непрозрачен: публичных полей с данными у него нет.
- Контекст сериализуем через ``to_token()`` / ``from_token()``. Токен
  содержит только служебные ссылки и не содержит открытый текст, ключевой
  материал, шифртекст и содержимое manifest.
- Контекст переживает перезапуск процесса, пока сохранены защищённые
  артефакты и ключ доступен в хранилище ключей.
- Контекст пригоден для повторного использования: восстановление
  идемпотентно и не изменяет артефакты.
- За удаление отвечает приложение-потребитель: явным вызовом освобождения
  ресурсов либо через контекстный менеджер. Автоматического удаления нет,
  иначе сломается сценарий отложенного ответа внешнего обработчика.
- Изоляция параллельных операций обеспечивается тем, что каждая подготовка
  получает собственный рабочий подкаталог.

Строки ``repr`` моделей не раскрывают текст, значения и содержимое контекста.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

from privacy_gateway.exceptions import RestoreError

__all__ = [
    "CONTEXT_FORMAT_VERSION",
    "GatewayConfig",
    "PreparedPayload",
    "RestoreContext",
    "RestoredPayload",
]

CONTEXT_FORMAT_VERSION = "1"


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Конфигурация библиотечного использования Privacy Gateway.

    routing_config_path:  Путь к YAML-конфигу маршрутизации; None — безопасные
                          умолчания библиотеки.
    entities_config_path: Путь к конфигу детектора; None — умолчание проекта.
    workspace_dir:        Рабочий каталог для защищённых артефактов. None —
                          библиотека создаёт временный каталог с правами
                          только для владельца и владеет им сама.
    strict:               Строгий режим восстановления (fail-closed).
                          Отключение допускается только явным решением
                          приложения-потребителя.
    keep_artifacts:       Сохранять артефакты после освобождения контекста.
    """

    routing_config_path: Path | None = None
    entities_config_path: Path | None = None
    workspace_dir: Path | None = None
    strict: bool = True
    keep_artifacts: bool = False


@dataclass(frozen=True, slots=True)
class RestoreContext:
    """Непрозрачный контекст восстановления.

    Приложение-потребитель передаёт объект обратно в ``restore`` без разбора
    и без интерпретации его содержимого. Публичных полей с данными нет:
    открытый текст, ключи, шифртекст, содержимое manifest и внутренние
    структуры детектора и токенизатора здесь не хранятся.
    """

    _handle: str
    _route_path: Path
    _workspace_dir: Path
    _owned_workspace: bool = False
    _correlation_id: str | None = None

    def __repr__(self) -> str:
        """Вернуть представление без раскрытия содержимого контекста."""
        return "RestoreContext(<opaque>)"

    def __str__(self) -> str:
        """Вернуть представление без раскрытия содержимого контекста."""
        return self.__repr__()

    def to_token(self) -> str:
        """Сериализовать контекст в строку для передачи между процессами.

        Токен содержит только служебные ссылки на защищённые артефакты и
        версию формата. Его следует хранить с теми же ограничениями доступа,
        что и сами артефакты.
        """
        payload: dict[str, object] = {
            "v": CONTEXT_FORMAT_VERSION,
            "handle": self._handle,
            "route": str(self._route_path),
            "workspace": str(self._workspace_dir),
            "owned": self._owned_workspace,
            "correlation_id": self._correlation_id,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @classmethod
    def from_token(cls, token: str) -> RestoreContext:
        """Восстановить контекст из токена ``to_token``.

        Raises:
            RestoreError: Токен повреждён, неполон либо имеет неподдерживаемую
                версию формата.
        """
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii"))
            decoded: object = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RestoreError(
                "Недействительный контекст восстановления."
            ) from exc

        if not isinstance(decoded, dict):
            raise RestoreError("Недействительный контекст восстановления.")
        if decoded.get("v") != CONTEXT_FORMAT_VERSION:
            raise RestoreError(
                "Неподдерживаемая версия контекста восстановления."
            )

        try:
            correlation_raw = decoded["correlation_id"]
            return cls(
                _handle=str(decoded["handle"]),
                _route_path=Path(str(decoded["route"])),
                _workspace_dir=Path(str(decoded["workspace"])),
                _owned_workspace=bool(decoded["owned"]),
                _correlation_id=(
                    None if correlation_raw is None else str(correlation_raw)
                ),
            )
        except KeyError as exc:
            raise RestoreError(
                "Контекст восстановления неполон."
            ) from exc


@dataclass(frozen=True, slots=True)
class PreparedPayload:
    """Результат подготовки текста для внешнего обработчика.

    text:           Защищённый текст без исходных значений.
    context:        Непрозрачный контекст восстановления.
    correlation_id: Идентификатор операции, заданный потребителем.
    token_count:    Количество подставленных токенов. Имена токенов наружу
                    не передаются.
    """

    text: str
    context: RestoreContext
    correlation_id: str | None = None
    token_count: int = 0

    def __repr__(self) -> str:
        """Вернуть представление без раскрытия текста."""
        return (
            "PreparedPayload("
            f"text_length={len(self.text)}, "
            f"token_count={self.token_count}, "
            f"has_correlation_id={self.correlation_id is not None}, "
            "context=<opaque>)"
        )

    def __str__(self) -> str:
        """Вернуть представление без раскрытия текста."""
        return self.__repr__()


@dataclass(frozen=True, slots=True)
class RestoredPayload:
    """Результат восстановления текста после внешнего обработчика.

    text:             Восстановленный текст. Содержит исходные значения и
                      требует той же защиты, что и исходный документ.
    correlation_id:   Идентификатор операции, заданный потребителем.
    tokens_restored:  Количество восстановленных токенов.
    tokens_missing:   Количество токенов, отсутствовавших в ответе внешнего
                      обработчика. Само по себе не является ошибкой.
    """

    text: str
    correlation_id: str | None = None
    tokens_restored: int = 0
    tokens_missing: int = 0

    def __repr__(self) -> str:
        """Вернуть представление без раскрытия восстановленного текста."""
        return (
            "RestoredPayload("
            f"text_length={len(self.text)}, "
            f"tokens_restored={self.tokens_restored}, "
            f"tokens_missing={self.tokens_missing}, "
            f"has_correlation_id={self.correlation_id is not None})"
        )

    def __str__(self) -> str:
        """Вернуть представление без раскрытия восстановленного текста."""
        return self.__repr__()
