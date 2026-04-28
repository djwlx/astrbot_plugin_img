from pathlib import Path
from typing import Any

PLUGIN_NAME = "astrbot_plugin_img"
PLUGIN_VERSION = "v1.3.0"

LEGACY_DATA_FILE = Path(__file__).parent / "config.json"
LEGACY_TRIGGER_DATA_FILENAME = "config.json"
TRIGGER_DATA_FILENAME = "triggers.json"

DEFAULT_ENABLE_IMAGE_COMPRESSION = True
DEFAULT_ENABLE_COMPRESS_RETRY = True
DEFAULT_RECALL_AFTER_SECONDS = 0


def read_bool_config(config: Any, key: str, default: bool) -> bool:
    if not hasattr(config, "get"):
        return default
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    return default


def read_non_negative_int_config(config: Any, key: str, default: int) -> int:
    if not hasattr(config, "get"):
        return default
    value = config.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default
