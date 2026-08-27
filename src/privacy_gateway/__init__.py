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

from privacy_gateway.exceptions import (
    ConfigurationError,
    DetectionError,
    IntegrityError,
    KeyStoreError,
    PrivacyGatewayError,
    RestoreError,
    StrictTokenError,
)
from privacy_gateway.facade import (
    GatewayConfig,
    PreparedPayload,
    PrivacyGateway,
    RestoreContext,
    RestoredPayload,
)

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
