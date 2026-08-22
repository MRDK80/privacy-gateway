"""Библиотечный API Privacy Gateway — публичные модели контракта.

Модуль задаёт стабильные типы, которыми внешнее приложение обменивается
с Privacy Gateway. Класс ``PrivacyGateway`` реализует жизненный цикл
prepare → внешний обработчик → restore поверх существующего конвейера
проекта. Реализованы ``prepare``, ``restore`` и освобождение ресурсов.

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
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from privacy_gateway import context_trust as _trust
from privacy_gateway import keystore as _keystore
from privacy_gateway import pipeline as _pipeline
from privacy_gateway import restore as _restore
from privacy_gateway import routing as _routing
from privacy_gateway.exceptions import (
    ConfigurationError,
    DetectionError,
    IntegrityError,
    KeyStoreError,
    RestoreError,
    StrictTokenError,
)
from privacy_gateway.models import ConfigurationError as _InternalConfigurationError
from privacy_gateway.models import ProcessingStatus as _ProcessingStatus
from privacy_gateway.models import RestoreStrictError as _InternalStrictError

__all__ = [
    "CONTEXT_FORMAT_VERSION",
    "GatewayConfig",
    "PreparedPayload",
    "PrivacyGateway",
    "RestoreContext",
    "RestoredPayload",
]

CONTEXT_FORMAT_VERSION = "2"

# Права рабочего каталога: только владелец.
_WORKSPACE_MODE = 0o700

# Нейтральная метка источника для библиотечных операций.
_SOURCE_REF = "library"


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

    Сериализованный контекст аутентифицирован (ADR-34): поля токена сами по
    себе не дают права на удаление рабочего каталога.
    """

    _handle: str
    _route_path: Path
    _workspace_dir: Path
    _base_dir: Path
    _owned_workspace: bool = False
    _correlation_id: str | None = None
    _signature: str | None = None

    def __repr__(self) -> str:
        """Вернуть представление без раскрытия содержимого контекста."""
        return "RestoreContext(<opaque>)"

    def __str__(self) -> str:
        """Вернуть представление без раскрытия содержимого контекста."""
        return self.__repr__()

    def _payload(self) -> dict[str, object]:
        """Вернуть подписываемую полезную нагрузку контекста."""
        return {
            "v": CONTEXT_FORMAT_VERSION,
            "handle": self._handle,
            "route": str(self._route_path),
            "workspace": str(self._workspace_dir),
            "base": str(self._base_dir),
            "owned": self._owned_workspace,
            "correlation_id": self._correlation_id,
        }

    def to_token(self) -> str:
        """Сериализовать контекст в строку для передачи между процессами.

        Токен содержит только служебные ссылки, версию формата и код
        аутентичности. Ключевой материал в токен не попадает. Токен следует
        хранить с теми же ограничениями доступа, что и сами артефакты.
        """
        payload: dict[str, object] = dict(self._payload())
        payload["sig"] = self._signature
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @classmethod
    def from_token(cls, token: str) -> RestoreContext:
        """Восстановить контекст из токена ``to_token``.

        Разбор структурный: код аутентичности переносится в объект и
        проверяется непосредственно перед операцией, которая на него
        опирается. Токены версии ``1`` не поддерживаются (ADR-34).

        Raises:
            RestoreError: Токен повреждён, неполон, не содержит кода
                аутентичности либо имеет неподдерживаемую версию формата.
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

        signature = decoded.get("sig")
        if not isinstance(signature, str) or not signature:
            raise RestoreError("Контекст восстановления неполон.")

        try:
            correlation_raw = decoded["correlation_id"]
            return cls(
                _handle=str(decoded["handle"]),
                _route_path=Path(str(decoded["route"])),
                _workspace_dir=Path(str(decoded["workspace"])),
                _base_dir=Path(str(decoded["base"])),
                _owned_workspace=bool(decoded["owned"]),
                _correlation_id=(
                    None if correlation_raw is None else str(correlation_raw)
                ),
                _signature=signature,
            )
        except KeyError as exc:
            raise RestoreError("Контекст восстановления неполон.") from exc


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


class PrivacyGateway:
    """Публичный фасад Privacy Gateway для библиотечной интеграции.

    Экземпляр не хранит изменяемого состояния между вызовами: конфигурация
    маршрутизации загружается на каждый вызов, ключ запрашивается на время
    операции, а каждая подготовка получает собственный рабочий подкаталог.
    Поэтому один экземпляр можно использовать из нескольких потоков.

    Экземпляр не логирует текст, значения, ключевой материал и содержимое
    защищённых артефактов.
    """

    def __init__(self, config: GatewayConfig | None = None) -> None:
        """Создать фасад с заданной конфигурацией."""
        self._config = config if config is not None else GatewayConfig()

    @property
    def config(self) -> GatewayConfig:
        """Вернуть конфигурацию фасада."""
        return self._config

    def prepare(
        self,
        text: str,
        *,
        correlation_id: str | None = None,
    ) -> PreparedPayload:
        """Подготовить текст для передачи внешнему обработчику.

        Args:
            text:           Исходный текст приложения-потребителя.
            correlation_id: Необязательный идентификатор операции. Не следует
                помещать в него чувствительные данные: он возвращается
                вызывающей стороне вместе с результатом.

        Returns:
            PreparedPayload с защищённым текстом и непрозрачным контекстом.

        Raises:
            ConfigurationError: Некорректная конфигурация либо недоступный
                рабочий каталог.
            KeyStoreError:      Ключ недоступен в хранилище ключей.
            DetectionError:     Подготовка остановлена по правилу fail-closed;
                защищённый текст не сформирован, артефакты удалены.
        """
        routing_cfg = self._load_routing()
        key = self._load_key()
        handle = secrets.token_hex(16)
        workspace = self._create_workspace(handle)

        try:
            result = _pipeline.prepare_pipeline(
                text=text,
                source_ref=_SOURCE_REF,
                routing_cfg=routing_cfg,
                key=key,
                out_dir=workspace,
                overwrite=False,
                entities_config_path=self._config.entities_config_path,
            )
        except _InternalConfigurationError as exc:
            self._remove_workspace(workspace)
            raise ConfigurationError(
                "Подготовка невозможна: некорректная конфигурация."
            ) from exc
        except OSError as exc:
            self._remove_workspace(workspace)
            raise ConfigurationError(
                "Не удалось записать защищённые артефакты."
            ) from exc

        if result.status is not _ProcessingStatus.OK:
            self._remove_workspace(workspace)
            raise DetectionError(
                "Подготовка остановлена: текст не признан безопасным.",
                status=str(result.status.value),
            )

        prompt_path = result.prompt_path
        route_path = result.route_path
        if prompt_path is None or route_path is None:
            self._remove_workspace(workspace)
            raise ConfigurationError(
                "Подготовка не сформировала защищённые артефакты."
            )

        try:
            protected_text = prompt_path.read_text(encoding="utf-8")
            token_count = result.token_count
        except (OSError, ValueError) as exc:
            self._remove_workspace(workspace)
            raise ConfigurationError(
                "Не удалось прочитать защищённые артефакты."
            ) from exc

        base_dir = _trust.resolve_trusted_base(self._config.workspace_dir)
        workspace_secret = _trust.new_workspace_secret()
        try:
            _trust.write_owner_marker(workspace, handle, workspace_secret)
        except OSError as exc:
            self._remove_workspace(workspace)
            raise ConfigurationError(
                "Не удалось закрепить владение рабочим каталогом."
            ) from exc

        unsigned = RestoreContext(
            _handle=handle,
            _route_path=route_path,
            _workspace_dir=workspace,
            _base_dir=base_dir,
            _owned_workspace=True,
            _correlation_id=correlation_id,
        )
        context = RestoreContext(
            _handle=handle,
            _route_path=route_path,
            _workspace_dir=workspace,
            _base_dir=base_dir,
            _owned_workspace=True,
            _correlation_id=correlation_id,
            _signature=_trust.sign_payload(
                unsigned._payload(), workspace_secret
            ),
        )
        return PreparedPayload(
            text=protected_text,
            context=context,
            correlation_id=correlation_id,
            token_count=token_count,
        )

    def restore(
        self,
        text: str,
        *,
        context: RestoreContext,
        correlation_id: str | None = None,
    ) -> RestoredPayload:
        """Восстановить исходные значения в ответе внешнего обработчика.

        Проверка целостности защищённых артефактов выполняется до возврата
        открытого текста. В строгом режиме недопустимые токены во внешнем
        ответе приводят к отказу, и открытый текст не возвращается.

        Args:
            text:           Ответ внешнего обработчика.
            context:        Непрозрачный контекст, полученный из ``prepare``.
            correlation_id: Идентификатор операции. По умолчанию берётся из
                контекста.

        Returns:
            RestoredPayload с восстановленным текстом и счётчиками токенов.

        Raises:
            RestoreError:     Контекст недействителен либо восстановление
                невозможно.
            StrictTokenError: Строгий режим: внешний ответ содержит
                неизвестные или искажённые токены.
            IntegrityError:   Проверка целостности не пройдена; открытый текст
                не возвращается.
            KeyStoreError:    Ключ недоступен в хранилище ключей.
        """
        effective_id = (
            correlation_id
            if correlation_id is not None
            else context._correlation_id
        )
        route_path = context._route_path
        if not route_path.is_file():
            raise RestoreError("Контекст восстановления недействителен.")

        try:
            result = _restore.restore_text(
                llm_response=text,
                route_path=route_path,
                manifest_path_override=None,
                strict=self._config.strict,
            )
        except _InternalStrictError as exc:
            raise StrictTokenError(
                "Строгий режим: внешний ответ содержит недопустимые токены."
            ) from exc
        except _keystore.KeystoreError as exc:
            raise KeyStoreError(
                "Ключ недоступен в хранилище ключей."
            ) from exc
        except _InternalConfigurationError as exc:
            raise IntegrityError(
                "Проверка целостности защищённых артефактов не пройдена."
            ) from exc
        except _restore.RestoreError as exc:
            raise RestoreError("Восстановление невозможно.") from exc
        except Exception as exc:  # noqa: BLE001
            raise RestoreError("Восстановление невозможно.") from exc

        restored_text = result.restored_text
        if restored_text is None:
            raise RestoreError("Восстановление не вернуло результат.")

        return RestoredPayload(
            text=restored_text,
            correlation_id=effective_id,
            tokens_restored=result.tokens_found_count,
            tokens_missing=result.tokens_missing_count,
        )

    def discard(self, context: RestoreContext) -> None:
        """Удалить защищённые артефакты, связанные с контекстом.

        Удаление выполняется только для контекста, который доказал владение
        рабочим каталогом: принадлежность доверенной базе этого фасада,
        canonical containment, настоящий каталог без ссылок, маркер владения
        с тем же handle и совпадение кода аутентичности (ADR-34). При любом
        отказе проверки не удаляется ничего.

        Проверка не обращается к хранилищу ключей: освобождение ресурсов
        остаётся возможным после ротации и удаления ключа.

        Вызов идемпотентен: если рабочий каталог уже удалён, вызов ничего не
        делает и не поднимает ошибку. При ``keep_artifacts`` артефакты
        сохраняются, и ответственность за их удаление остаётся на
        приложении-потребителе.

        Raises:
            RestoreError: Контекст не принадлежит этому фасаду, изменён либо
                не подтверждён как владеющий рабочим каталогом.
        """
        if not context._owned_workspace:
            raise RestoreError(
                "Контекст не управляется этим экземпляром Privacy Gateway."
            )

        if self._config.keep_artifacts:
            return

        workspace = self._validated_workspace(context)
        if workspace is None:
            return

        identity = _trust.workspace_identity(workspace)
        try:
            if _trust.workspace_identity(workspace) != identity:
                raise RestoreError(
                    "Рабочий каталог изменился во время проверки."
                )
        except _trust.ContextTrustError as exc:
            raise RestoreError(
                "Рабочий каталог изменился во время проверки."
            ) from exc

        self._remove_workspace(workspace)

    def _validated_workspace(self, context: RestoreContext) -> Path | None:
        """Проверить владение и вернуть каталог, разрешённый к удалению.

        Возвращает ``None``, если удалять нечего. Порядок проверок задан
        fail-closed: доверенная база берётся из конфигурации фасада, а не из
        токена, и только подтверждённый каталог доходит до удаления.
        """
        expected_base = _trust.resolve_trusted_base(self._config.workspace_dir)
        if _trust.canonical_path(context._base_dir) != expected_base:
            raise RestoreError(
                "Контекст восстановления не признан доверенным."
            )

        workspace = context._workspace_dir
        if not _trust.is_contained(
            expected_base, _trust.canonical_path(workspace)
        ):
            raise RestoreError(
                "Контекст восстановления не признан доверенным."
            )

        try:
            workspace.lstat()
        except OSError:
            return None

        if not _trust.is_real_directory(workspace):
            raise RestoreError(
                "Рабочий каталог не признан принадлежащим контексту."
            )

        secret = _trust.read_owner_secret(workspace, context._handle)
        if secret is None:
            raise RestoreError(
                "Рабочий каталог не признан принадлежащим контексту."
            )

        if not _trust.verify_payload(
            context._payload(), context._signature, secret
        ):
            raise RestoreError(
                "Контекст восстановления не признан доверенным."
            )

        return workspace

    def _load_routing(self) -> _routing.RoutingConfig:
        """Загрузить конфигурацию маршрутизации для одной операции."""
        try:
            return _routing.load_routing_config(self._config.routing_config_path)
        except _InternalConfigurationError as exc:
            raise ConfigurationError(
                "Некорректная конфигурация маршрутизации."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ConfigurationError(
                "Не удалось загрузить конфигурацию маршрутизации."
            ) from exc

    def _load_key(self) -> bytes:
        """Получить активный ключ на время одной операции."""
        try:
            return _keystore.get_key()
        except _keystore.KeystoreError as exc:
            raise KeyStoreError(
                "Ключ недоступен в хранилище ключей."
            ) from exc

    def _create_workspace(self, handle: str) -> Path:
        """Создать изолированный рабочий подкаталог для одной операции."""
        base = self._config.workspace_dir
        try:
            if base is None:
                return Path(tempfile.mkdtemp(prefix="pgw-"))
            base.mkdir(parents=True, exist_ok=True)
            workspace = base / f"pgw-{handle}"
            workspace.mkdir(mode=_WORKSPACE_MODE)
            return workspace
        except OSError as exc:
            raise ConfigurationError("Рабочий каталог недоступен.") from exc

    @staticmethod
    def _remove_workspace(workspace: Path) -> None:
        """Удалить рабочий подкаталог операции, не поднимая ошибок."""
        shutil.rmtree(workspace, ignore_errors=True)
