import json
import logging
from pathlib import Path
from typing import Dict, Optional

import aiohttp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

DATA_FILE = Path(__file__).parent / "config.json"

logger = logging.getLogger(__name__)


def normalize_trigger(trigger: str) -> str:
    return trigger.strip().lower()


def is_http_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def load_trigger_map(config_path: Path = DATA_FILE) -> Dict[str, str]:
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


def save_trigger_map(trigger_map: Dict[str, str], config_path: Path = DATA_FILE) -> None:
    data = {"trigger_map": trigger_map}
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


@register("astrbot_plugin_img", "djwlx", "自定义图片触发词插件，仅支持直接图片URL", "v1.1.0")
class DirectImageTriggerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.trigger_map: Dict[str, str] = load_trigger_map()

    @filter.command("添加图片触发词")
    async def add_image_trigger(self, event: AstrMessageEvent, trigger: str, url: str):
        trigger_key = normalize_trigger(trigger)
        image_url = url.strip()

        if not trigger_key:
            yield event.plain_result("触发词不能为空")
            return
        if not is_http_url(image_url):
            yield event.plain_result("图片 URL 必须以 http:// 或 https:// 开头")
            return

        self.trigger_map[trigger_key] = image_url
        save_trigger_map(self.trigger_map)
        yield event.plain_result(f"已添加图片触发词: {trigger_key}")

    @filter.command("删除图片触发词")
    async def delete_image_trigger(self, event: AstrMessageEvent, trigger: str):
        trigger_key = normalize_trigger(trigger)
        if trigger_key not in self.trigger_map:
            yield event.plain_result(f"触发词不存在: {trigger_key}")
            return

        del self.trigger_map[trigger_key]
        save_trigger_map(self.trigger_map)
        yield event.plain_result(f"已删除图片触发词: {trigger_key}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        trigger_key = normalize_trigger(event.message_str)
        if trigger_key.startswith("/"):
            return
        if trigger_key not in self.trigger_map:
            return

        image_url = await self._resolve_direct_image_url(self.trigger_map[trigger_key])
        if image_url:
            yield event.chain_result([Image(file=image_url)])
            event.stop_event()
        else:
            yield event.plain_result("获取图片失败，请检查图片 URL 是否有效")

    async def _resolve_direct_image_url(self, url: str) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return None

                    content_type = resp.headers.get("Content-Type", "").lower()
                    if not content_type.startswith("image/"):
                        return None

                    return str(resp.url)
        except Exception as exc:
            logger.error("获取图片失败: %s", exc)
            return None
