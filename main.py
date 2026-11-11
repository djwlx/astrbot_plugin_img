import asyncio
import errno
import io
import logging
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

import aiohttp
from PIL import Image as PILImage
from PIL import ImageOps, UnidentifiedImageError, features

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register

try:
    from .trigger_store import (
        get_plugin_data_dir,
        is_http_url,
        load_trigger_map,
        normalize_trigger,
        save_trigger_map,
    )
except ImportError:
    if __package__:
        raise
    from trigger_store import (
        get_plugin_data_dir,
        is_http_url,
        load_trigger_map,
        normalize_trigger,
        save_trigger_map,
    )

logger = logging.getLogger(__name__)

PLUGIN_NAME = "astrbot_plugin_img"
PLUGIN_VERSION = "v2.1.7"
TRIGGER_DATA_FILENAME = "triggers.json"
MAX_IMAGE_BYTES = 4_000_000

# 按插件约定允许任意像素数的图片进入压缩，不使用 Pillow 的像素阈值拦截。
PILImage.MAX_IMAGE_PIXELS = None

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}


def content_type_to_extension(content_type: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return CONTENT_TYPE_EXTENSIONS.get(media_type, ".jpg")


class ImageCompressionError(ValueError):
    """只携带可安全展示给用户的压缩失败原因。"""


@register(PLUGIN_NAME, "djwlx", "自定义图片触发词插件，仅支持直接图片URL", PLUGIN_VERSION)
class DirectImageTriggerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

        self.data_dir = get_plugin_data_dir(PLUGIN_NAME)
        self.trigger_file = self.data_dir / TRIGGER_DATA_FILENAME
        self.temp_dir = self.data_dir / "temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.trigger_map: Dict[str, str] = load_trigger_map(self.trigger_file)
        self._compression_lock = asyncio.Lock()

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
        save_trigger_map(self.trigger_map, self.trigger_file)
        yield event.plain_result(f"已添加图片触发词: {trigger_key}")

    @filter.command("删除图片触发词")
    async def delete_image_trigger(self, event: AstrMessageEvent, trigger: str):
        trigger_key = normalize_trigger(trigger)
        if trigger_key not in self.trigger_map:
            yield event.plain_result(f"触发词不存在: {trigger_key}")
            return

        del self.trigger_map[trigger_key]
        save_trigger_map(self.trigger_map, self.trigger_file)
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

        image_path = await self._download_direct_image(self.trigger_map[trigger_key])
        if not image_path:
            await self._safe_send_plain(event, "获取图片失败，请检查图片 URL 是否有效")
            event.stop_event()
            return

        original_path = image_path
        try:
            async with self._compression_lock:
                image_path = await asyncio.to_thread(
                    self._compress_image_to_webp, image_path
                )
        except Exception as exc:
            if isinstance(exc, ImageCompressionError):
                reason = str(exc)
            elif isinstance(exc, UnidentifiedImageError):
                reason = "图片格式无法识别或文件已损坏"
            elif isinstance(exc, MemoryError):
                reason = "压缩时内存不足"
            elif isinstance(exc, FileNotFoundError):
                reason = "下载的图片文件不存在"
            elif isinstance(exc, PermissionError):
                reason = "临时图片目录没有读写权限"
            elif isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                reason = "磁盘空间不足，无法保存压缩图片"
            else:
                reason = f"图片解码、编码或文件读写异常（{type(exc).__name__}）"
            logger.warning("图片压缩失败: %s [%s]", reason, type(exc).__name__)
            await self._safe_send_plain(event, f"图片压缩失败：{reason}")
            event.stop_event()
            return

        try:
            await self._send_image_path(event, image_path)
        except Exception as exc:
            logger.warning("发送图片失败: 异常类型=%s", type(exc).__name__)
            await self._safe_send_plain(event, "发送图片失败，请稍后重试")
        else:
            self._cleanup_image_files(original_path, image_path)

        event.stop_event()

    async def _download_direct_image(self, url: str) -> Optional[str]:
        started = time.perf_counter()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("获取图片失败: HTTP 状态码=%s", resp.status)
                        return None

                    content_type = resp.headers.get("Content-Type", "").lower()
                    ext = content_type_to_extension(content_type)
                    image_path = self.temp_dir / f"{uuid.uuid4()}{ext}"
                    data = await resp.read()
                    with open(image_path, "wb") as f:
                        f.write(data)
                    logger.info(
                        "获取图片成功: 字节数=%d 耗时=%.2f秒",
                        len(data), time.perf_counter() - started,
                    )
                    return str(image_path)
        except asyncio.TimeoutError:
            logger.warning("获取图片失败: 下载超时（20秒）")
            return None
        except Exception as exc:
            logger.error("获取图片失败: 异常类型=%s", type(exc).__name__)
            return None

    def _compress_image_to_webp(self, image_path: str) -> str:
        started = time.perf_counter()
        source_bytes = Path(image_path).stat().st_size
        if source_bytes <= MAX_IMAGE_BYTES:
            logger.info("图片无需压缩: 字节数=%d，使用原图", source_bytes)
            return image_path
        if not features.check("webp"):
            raise ImageCompressionError("当前 Pillow 缺少 WebP 编码支持")

        frames = []
        durations = []
        try:
            with PILImage.open(image_path) as image:
                frame_count = (
                    image.n_frames if getattr(image, "is_animated", False) else 1
                )
                logger.info(
                    "开始图片压缩: 原始字节数=%d 尺寸=%d×%d 帧数=%d",
                    source_bytes, image.width, image.height, frame_count,
                )
                loop = image.info.get("loop", 0)
                for index in range(frame_count):
                    image.seek(index)
                    frame = ImageOps.exif_transpose(image).convert("RGBA")
                    # WebP 单边最大为 16383 像素，超长图片先等比缩小。
                    frame.thumbnail((16383, 16383), PILImage.Resampling.LANCZOS)
                    frames.append(frame)
                    durations.append(max(1, int(image.info.get("duration", 100))))

            while True:
                for quality in (85, 65, 45, 25):
                    with io.BytesIO() as output:
                        frames[0].save(
                            output,
                            format="WEBP",
                            quality=quality,
                            method=4,
                            save_all=len(frames) > 1,
                            append_images=frames[1:],
                            duration=durations,
                            loop=loop,
                        )
                        if 0 < output.tell() <= MAX_IMAGE_BYTES:
                            target_path = self.temp_dir / f"{uuid.uuid4()}.webp"
                            with open(target_path, "xb") as target:
                                target.write(output.getbuffer())
                            logger.info(
                                "图片压缩成功: 原始字节数=%d 压缩后字节数=%d "
                                "尺寸=%d×%d 帧数=%d 质量=%d 耗时=%.2f秒",
                                source_bytes, output.tell(),
                                frames[0].width, frames[0].height, len(frames), quality,
                                time.perf_counter() - started,
                            )
                            return str(target_path)

                width, height = frames[0].size
                if (width, height) == (1, 1):
                    raise ImageCompressionError("缩小至最小尺寸后文件仍超过 4 MB")
                size = (max(1, int(width * 0.75)), max(1, int(height * 0.75)))
                for index, frame in enumerate(frames):
                    frames[index] = frame.resize(size, PILImage.Resampling.LANCZOS)
                    frame.close()
        finally:
            for frame in frames:
                frame.close()

    async def _send_image_path(self, event: AstrMessageEvent, image_path: str) -> None:
        image_bytes = Path(image_path).stat().st_size
        if image_bytes > MAX_IMAGE_BYTES:
            raise ValueError("图片文件超过 4 MB，拒绝发送")
        await event.send(MessageChain([Image.fromFileSystem(image_path)]))
        logger.info("发送图片成功: 字节数=%d", image_bytes)

    def _cleanup_image_files(self, *image_paths: str) -> None:
        for image_path in dict.fromkeys(image_paths):
            try:
                path = Path(image_path)
                if path.is_symlink() or path.parent.resolve() != self.temp_dir.resolve():
                    logger.warning("跳过非插件临时文件清理")
                    continue
                path.unlink(missing_ok=True)
                logger.info("图片临时文件清理完成")
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("清理图片临时文件失败: %s", type(exc).__name__)

    async def _safe_send_plain(self, event: AstrMessageEvent, text: str) -> None:
        try:
            await event.send(MessageChain([Plain(text)]))
            logger.info("发送文本提示成功")
        except Exception as exc:
            logger.error("发送文本提示失败: 异常类型=%s", type(exc).__name__)
