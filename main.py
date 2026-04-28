import asyncio
import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, register

DATA_FILE = Path(__file__).parent / "config.json"
TEMP_DIR = Path(__file__).parent / "temp"
TELEGRAM_PHOTO_SAFE_SIDE_SUM = 9900
TELEGRAM_PHOTO_SAFE_RATIO = 19.5

logger = logging.getLogger(__name__)

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


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


def content_type_to_extension(content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return CONTENT_TYPE_EXTENSIONS.get(media_type, ".jpg")


def safe_photo_canvas_size(width: int, height: int) -> Tuple[int, int]:
    target_width = width
    target_height = height

    if target_width > target_height * TELEGRAM_PHOTO_SAFE_RATIO:
        target_height = math.ceil(target_width / TELEGRAM_PHOTO_SAFE_RATIO)
    elif target_height > target_width * TELEGRAM_PHOTO_SAFE_RATIO:
        target_width = math.ceil(target_height / TELEGRAM_PHOTO_SAFE_RATIO)

    side_sum = target_width + target_height
    if side_sum > TELEGRAM_PHOTO_SAFE_SIDE_SUM:
        scale = TELEGRAM_PHOTO_SAFE_SIDE_SUM / side_sum
        target_width = max(1, math.floor(target_width * scale))
        target_height = max(1, math.floor(target_height * scale))

    return target_width, target_height


@register("astrbot_plugin_img", "djwlx", "自定义图片触发词插件，仅支持直接图片URL", "v1.1.0")
class DirectImageTriggerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        TEMP_DIR.mkdir(exist_ok=True)
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

    @filter.command("图片触发词列表")
    async def list_image_triggers(self, event: AstrMessageEvent):
        if not self.trigger_map:
            yield event.plain_result("暂无图片触发词")
            return

        triggers = "\n".join(sorted(self.trigger_map))
        yield event.plain_result(f"图片触发词列表:\n{triggers}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        trigger_key = normalize_trigger(event.message_str)
        if trigger_key.startswith("/"):
            return
        if trigger_key not in self.trigger_map:
            return

        image_path, cleanup_paths = await self._prepare_image_file(
            self.trigger_map[trigger_key]
        )
        if image_path:
            yield event.chain_result([Image.fromFileSystem(image_path)])
            event.stop_event()
            self._schedule_cleanup(cleanup_paths)
        else:
            yield event.plain_result("获取图片失败，请检查图片 URL 是否有效")

    async def _prepare_image_file(self, url: str) -> Tuple[Optional[str], List[str]]:
        image_path = await self._download_direct_image(url)
        if not image_path:
            return None, []

        cleanup_paths = [image_path]
        normalized_path = self._normalize_photo_dimensions(image_path)
        if normalized_path != image_path:
            cleanup_paths.append(normalized_path)

        return normalized_path, cleanup_paths

    async def _download_direct_image(self, url: str) -> Optional[str]:
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

                    ext = content_type_to_extension(content_type)
                    image_path = TEMP_DIR / f"{uuid.uuid4()}{ext}"
                    data = await resp.read()
                    with open(image_path, "wb") as f:
                        f.write(data)
                    return str(image_path)
        except Exception as exc:
            logger.error("获取图片失败: %s", exc)
            return None

    def _normalize_photo_dimensions(self, image_path: str) -> str:
        if image_path.lower().endswith(".gif"):
            return image_path

        try:
            from PIL import Image as PILImage
            from PIL import ImageOps, UnidentifiedImageError
        except Exception as exc:
            logger.warning("Pillow 未安装，跳过图片尺寸处理: %s", exc)
            return image_path

        try:
            with PILImage.open(image_path) as image:
                if getattr(image, "is_animated", False):
                    return image_path

                image = ImageOps.exif_transpose(image)
                width, height = image.size
                if width <= 0 or height <= 0:
                    return image_path

                target_width, target_height = safe_photo_canvas_size(width, height)
                needs_canvas = (target_width, target_height) != (width, height)
                has_alpha = image.mode in ("RGBA", "LA") or (
                    image.mode == "P" and "transparency" in image.info
                )

                if not needs_canvas:
                    return image_path

                image = image.convert("RGBA" if has_alpha else "RGB")
                canvas_mode = "RGBA" if has_alpha else "RGB"
                background = (255, 255, 255, 0) if has_alpha else (255, 255, 255)
                canvas = PILImage.new(
                    canvas_mode, (target_width, target_height), background
                )

                scale = min(target_width / width, target_height / height, 1)
                resized_width = max(1, math.floor(width * scale))
                resized_height = max(1, math.floor(height * scale))
                if (resized_width, resized_height) != (width, height):
                    image = image.resize(
                        (resized_width, resized_height), PILImage.Resampling.LANCZOS
                    )

                left = (target_width - resized_width) // 2
                top = (target_height - resized_height) // 2
                canvas.paste(image, (left, top), image if has_alpha else None)

                suffix = ".png" if has_alpha else ".jpg"
                normalized_path = TEMP_DIR / f"{uuid.uuid4()}_normalized{suffix}"
                if has_alpha:
                    canvas.save(normalized_path, "PNG", optimize=True)
                else:
                    canvas.save(normalized_path, "JPEG", quality=90, optimize=True)
                return str(normalized_path)
        except UnidentifiedImageError as exc:
            logger.warning("图片格式无法识别，跳过尺寸处理: %s", exc)
            return image_path
        except Exception as exc:
            logger.warning("图片尺寸处理失败，使用原图: %s", exc)
            return image_path

    def _schedule_cleanup(self, file_paths: List[str]):
        async def delayed_cleanup():
            await asyncio.sleep(60)
            for file_path in file_paths:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as exc:
                    logger.error("清理临时文件失败: %s", exc)

        asyncio.create_task(delayed_cleanup())
