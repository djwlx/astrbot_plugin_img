import json
from pathlib import Path
from typing import Any, Dict

from astrbot.api import logger
from astrbot.api.star import StarTools


def normalize_trigger(trigger: Any) -> str:
    if not isinstance(trigger, str):
        return ""
    return trigger.strip().lower()


def is_http_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def get_plugin_data_dir(plugin_name: str) -> Path:
    try:
        return StarTools.get_data_dir(plugin_name)
    except Exception:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        data_dir = Path(get_astrbot_plugin_data_path()) / plugin_name
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir


def load_trigger_map(config_path: Path) -> Dict[str, str]:
    if not config_path.exists():
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.error("加载配置失败: 异常类型=%s", type(exc).__name__)
        return {}

    raw_map = data.get("trigger_map", {})
    if not isinstance(raw_map, dict):
        return {}

    return {
        normalize_trigger(trigger): url.strip()
        for trigger, url in raw_map.items()
        if (
            isinstance(trigger, str)
            and isinstance(url, str)
            and normalize_trigger(trigger)
            and is_http_url(url.strip())
        )
    }


def save_trigger_map(trigger_map: Dict[str, str], config_path: Path) -> None:
    data = {"trigger_map": trigger_map}
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
