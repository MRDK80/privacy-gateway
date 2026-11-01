"""Compatibility imports; admission calls the frozen implementation directly."""

from tools.agent_epic_runtime_cli import (
    _final_preseed_CONFIG8_KEYS as CONFIG8_KEYS,
)
from tools.agent_epic_runtime_cli import (
    _final_preseed_content as _content,
)
from tools.agent_epic_runtime_cli import (
    validate_final_preseed as validate_preseed,
)

__all__ = ["CONFIG8_KEYS", "_content", "validate_preseed"]
