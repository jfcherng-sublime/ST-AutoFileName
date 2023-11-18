from __future__ import annotations

from .auto_file_path import (
    AfpDeletePrefixedSlash,
    AfpSettingsPanel,
    AfpShowFilenames,
    FileNameComplete,
    InsertDimensionsCommand,
    ReloadAutoCompleteCommand,
)

__all__ = (
    "AfpDeletePrefixedSlash",
    "AfpSettingsPanel",
    "AfpShowFilenames",
    "FileNameComplete",
    "InsertDimensionsCommand",
    "ReloadAutoCompleteCommand",
)


def plugin_loaded() -> None:
    """Executed when this plugin is loaded."""


def plugin_unloaded() -> None:
    """Executed when this plugin is unloaded."""
