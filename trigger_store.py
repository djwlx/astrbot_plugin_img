import json
import logging
from pathlib import Path
from typing import Dict, List

from astrbot.api.star import StarTools

from settings import PLUGIN_NAME

logger = logging.getLogger(__name__)


def normalize_trigger(trigger: str) -> str:
    return trigger.strip().lower()


def is_http_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def get_plugin_data_dir(plugin_name: str = PLUGIN_NAME) -> Path:
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
        logger.error("加载配置失败: %s", exc)
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


def migrate_legacy_trigger_data(source_paths: List[Path], target_path: Path) -> bool:
    if target_path.exists():
        return False

    for source_path in source_paths:
        if not source_path.exists():
            continue
        if source_path.resolve() == target_path.resolve():
            continue

        trigger_map = load_trigger_map(source_path)
        if not trigger_map:
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        save_trigger_map(trigger_map, target_path)
        return True

    return False
