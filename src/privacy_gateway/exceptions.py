"""Stable public exceptions for Privacy Gateway integrations.

Import from the top-level package, for example:
    from privacy_gateway import IntegrityError, PrivacyGatewayError

All public errors derive from PrivacyGatewayError. Internal exceptions are not
public contracts; the facade maps them using raise PublicError(...) from exc.
Public messages exclude plaintext, keys, ciphertext, token names and manifest
contents. This module does not change CLI behavior or process exit codes.
"""

from __future__ import annotations

__all__ = [
    "ConfigurationError",
    "DetectionError",
    "IntegrityError",
    "KeyStoreError",
    "PrivacyGatewayError",
    "RestoreError",
    "StrictTokenError",
]


class PrivacyGatewayError(Exception):
    """Base public library error.

    Catch this class to handle the library's public error hierarchy.
    """


class ConfigurationError(PrivacyGatewayError):
    """Invalid library/file configuration or unavailable workspace.

    Raised before text processing for unknown configuration keys, invalid settings
    or an inaccessible working directory.
    """


class DetectionError(PrivacyGatewayError):
    """Preparation stopped fail closed, without returning protected text.

    A secret, unconditional blocking rule or failed independent validation caused
    rejection. The neutral status attribute (such as BLOCKED or PENDING) does not
    contain detected source values.
    """

    def __init__(self, message: str, *, status: str | None = None) -> None:
        super().__init__(message)
        self.status: str | None = status


class KeyStoreError(PrivacyGatewayError):
    """The key is absent, the store unavailable or its backend disallowed.

    Public error messages do not contain key material.
    """


class IntegrityError(PrivacyGatewayError):
    """Protected-artifact integrity or authenticated decryption failed.

    Raised before returning plaintext for mismatched/corrupt artifacts, checksum
    failure or inability to authenticate decryption.
    """


class RestoreError(PrivacyGatewayError):
    """Restoration is impossible.

    The context is invalid, its format unsupported, or another restoration
    failure prevents completion.
    """


class StrictTokenError(RestoreError):
    """Strict restoration rejected unknown or malformed response tokens.

    The protected structure is valid but the response contains unacceptable tokens.
    This derives from RestoreError so callers may catch the broad restoration
    category or handle this case separately.
    """
