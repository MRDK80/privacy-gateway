"""Privacy Gateway: local text protection before external processing.

Stable library imports are exposed from the top-level package, including
GatewayConfig, PreparedPayload, PrivacyGateway, PrivacyGatewayError,
RestoreContext and RestoredPayload. The lifecycle is prepare -> external
processing of protected text only -> restore -> explicit discard.
Names outside ``__all__`` are internal and may change without notice.
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
