"""Privacy Gateway — локальная защита текста перед передачей внешнему обработчику.

Стабильный публичный путь импорта библиотечного API:

    from privacy_gateway import (
        GatewayConfig,
        PreparedPayload,
        PrivacyGateway,
        PrivacyGatewayError,
        RestoreContext,
        RestoredPayload,
    )

Жизненный цикл: ``prepare`` → внешний обработчик → ``restore``. Всё, что не
перечислено в ``__all__``, является внутренней реализацией и может меняться
без предупреждения.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from privacy_gateway.exceptions import (
    ConfigurationError,
    DetectionError,
    IntegrityError,
    KeyStoreError,
    PrivacyGatewayError,
    RestoreError,
    StrictTokenError,
)

if TYPE_CHECKING:
    from privacy_gateway.facade import (
        GatewayConfig,
        PreparedPayload,
        PrivacyGateway,
        RestoreContext,
        RestoredPayload,
    )


_FACADE_EXPORTS = frozenset({
    "GatewayConfig", "PreparedPayload", "PrivacyGateway",
    "RestoreContext", "RestoredPayload",
})


def __getattr__(name: str) -> Any:
    if name not in _FACADE_EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module("privacy_gateway.facade"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | _FACADE_EXPORTS)


__version__ = "0.5.0"

__all__ = [
    "ConfigurationError",
    "DetectionError",
    "GatewayConfig",
    "IntegrityError",
    "KeyStoreError",
    "PreparedPayload",
    "PrivacyGateway",
    "PrivacyGatewayError",
    "RestoreContext",
    "RestoreError",
    "RestoredPayload",
    "StrictTokenError",
    "__version__",
]
