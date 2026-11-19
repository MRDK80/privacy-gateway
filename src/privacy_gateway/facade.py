"""Privacy Gateway library facade and stable public models.

PrivacyGateway provides prepare -> external processing -> restore, followed by
explicit discard. Only protected text is intended for the external consumer.
Context is opaque and serializable through to_token/from_token; it contains
references, format version and authentication code, not plaintext, keys,
ciphertext or manifest contents. It survives process restart while protected
artifacts remain and the required key is available. Restore is repeatable and
does not modify artifacts. The consumer releases resources explicitly with
discard; delayed external responses preclude automatic deletion. There is no
facade context-manager API. Each prepare gets an isolated workspace. Public
model repr/str exclude text, values and protected context contents.
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
    """Immutable library configuration.

    routing_config_path: Optional routing YAML; None uses safe library defaults.
    entities_config_path: Optional detector configuration; None uses project defaults.
    workspace_dir: Optional artifact base; None creates an owned temporary directory.
    strict: Strict fail-closed restoration, disabled only by explicit consumer choice.
    keep_artifacts: Preserve artifacts when releasing context; removal is the
        consumer's responsibility.
    """

    routing_config_path: Path | None = None
    entities_config_path: Path | None = None
    workspace_dir: Path | None = None
    strict: bool = True
    keep_artifacts: bool = False


@dataclass(frozen=True, slots=True)
class RestoreContext:
    """Opaque restoration context, passed back to restore without interpretation.

    No public data fields expose plaintext, keys, ciphertext, manifest contents or
    internal detector/tokenizer structures. Serialized context is authenticated
    (ADR-34); token fields alone never grant workspace-deletion authority.
    """

    _handle: str
    _route_path: Path
    _workspace_dir: Path
    _base_dir: Path
    _owned_workspace: bool = False
    _correlation_id: str | None = None
    _signature: str | None = None

    def __repr__(self) -> str:
        """Return a representation without protected text or context contents."""
        return "RestoreContext(<opaque>)"

    def __str__(self) -> str:
        """Return a representation without protected text or context contents."""
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
        """Serialize context for transfer between local processes.

        The token contains references, format version and an authentication code, not
        key material. Protect access as carefully as access to the artifacts.
        """
        payload: dict[str, object] = dict(self._payload())
        payload["sig"] = self._signature
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @classmethod
    def from_token(cls, token: str) -> RestoreContext:
        """Parse a token produced by to_token.

        Structural parsing carries the authentication code into the object; it is
        verified immediately before an operation relying on it. Version 1 tokens are
        unsupported (ADR-34).

        Raises:
            RestoreError: Malformed/incomplete token, missing authentication code or
                unsupported format version.
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
    """Preparation result for external processing.

    text: Protected text; detection limits still apply.
    context: Opaque restoration context; keep local.
    correlation_id: Consumer-supplied identifier; do not place sensitive data here.
    token_count: Number of substituted tokens; names are not exposed by this model.
    Send only the intended text, never the whole payload, to an external consumer.
    """

    text: str
    context: RestoreContext
    correlation_id: str | None = None
    token_count: int = 0

    def __repr__(self) -> str:
        """Return a representation without protected text or context contents."""
        return (
            "PreparedPayload("
            f"text_length={len(self.text)}, "
            f"token_count={self.token_count}, "
            f"has_correlation_id={self.correlation_id is not None}, "
            "context=<opaque>)"
        )

    def __str__(self) -> str:
        """Return a representation without protected text or context contents."""
        return self.__repr__()


@dataclass(frozen=True, slots=True)
class RestoredPayload:
    """Restoration result after external processing.

    text: Restored plaintext with source values; protect it like the original.
    correlation_id: Consumer-supplied operation identifier.
    tokens_restored: Number of restored tokens.
    tokens_missing: Tokens absent from the response, not an error by itself.
    """

    text: str
    correlation_id: str | None = None
    tokens_restored: int = 0
    tokens_missing: int = 0

    def __repr__(self) -> str:
        """Return a representation without protected text or context contents."""
        return (
            "RestoredPayload("
            f"text_length={len(self.text)}, "
            f"tokens_restored={self.tokens_restored}, "
            f"tokens_missing={self.tokens_missing}, "
            f"has_correlation_id={self.correlation_id is not None})"
        )

    def __str__(self) -> str:
        """Return a representation without protected text or context contents."""
        return self.__repr__()


class PrivacyGateway:
    """Public library facade for Privacy Gateway.

    No mutable operation state is retained across calls. Routing configuration is
    loaded per call, keys are requested per operation, and each preparation gets
    its own workspace. One instance may be used by multiple threads. It does not
    log text, source values, keys or protected-artifact contents.
    """

    def __init__(self, config: GatewayConfig | None = None) -> None:
        """Create the facade with the selected configuration."""
        self._config = config if config is not None else GatewayConfig()

    @property
    def config(self) -> GatewayConfig:
        """Return the facade configuration."""
        return self._config

    def prepare(
        self,
        text: str,
        *,
        correlation_id: str | None = None,
    ) -> PreparedPayload:
        """Prepare text for external processing.

        Args:
            text: Consumer input text.
            correlation_id: Optional non-sensitive identifier returned to the caller.

        Returns:
            PreparedPayload with protected text and an opaque local context.

        Raises:
            ConfigurationError: Invalid configuration or unavailable workspace.
            KeyStoreError: Key unavailable in the key store.
            DetectionError: Fail-closed preparation; no protected payload is returned.
                Failure cleanup follows the documented artifact-cleanup guarantees.
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
        """Restore source values in an external response.

        Artifact integrity is checked before plaintext is returned. In strict mode,
        invalid response tokens cause rejection rather than returning plaintext.

        Args:
            text: Untrusted external response.
            context: Opaque context returned by prepare.
            correlation_id: Optional identifier, defaulting to the context's value.

        Returns:
            RestoredPayload containing sensitive plaintext and token counts.

        Raises:
            RestoreError: Invalid context or restoration cannot complete.
            StrictTokenError: Unknown or malformed response token in strict mode.
            IntegrityError: Artifact validation failed; no plaintext returned.
            KeyStoreError: Key unavailable in the key store.
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
        """Release protected artifacts after verifying context ownership.

        Checks bind this facade's trusted base, containment, an actual directory without
        links, ownership marker and authentication code to the pinned filesystem object
        (ADR-34). Unverified context does not authorize recursive deletion. Removal uses
        the verified entity, not a newly resolved path; platform mechanisms differ.
        Failure to prove pinning fails closed and may leave a quarantine directory under
        the documented threat model. No key-store access is needed after rotation or
        key deletion. Already removed workspaces are idempotent, without an error.
        With keep_artifacts enabled, the consumer remains responsible for removal.

        Raises:
            RestoreError: Context foreign, modified or not verified to own
                its workspace.
        """
        if not context._owned_workspace:
            raise RestoreError(
                "Контекст не управляется этим экземпляром Privacy Gateway."
            )

        if self._config.keep_artifacts:
            return

        validated = self._validated_workspace(context)
        if validated is None:
            return
        workspace, secret = validated

        try:
            _trust.remove_verified_workspace(
                workspace, context._handle, secret
            )
        except _trust.ContextTrustError as exc:
            raise RestoreError(
                "Рабочий каталог изменился во время проверки."
            ) from exc

    def _validated_workspace(
        self, context: RestoreContext
    ) -> tuple[Path, str] | None:
        """Проверить владение и вернуть каталог с секретом владения.

        Возвращает ``None``, если удалять нечего. Порядок проверок задан
        fail-closed: доверенная база берётся из конфигурации фасада, а не из
        токена, и только подтверждённый каталог доходит до удаления. Секрет
        возвращается, чтобы граница удаления привязала аутентичность
        контекста к закреплённой сущности, а не к пути.
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

        return workspace, secret

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
