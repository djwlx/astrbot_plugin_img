import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from .image_files import (
        ImageFileService,
        content_type_to_extension,
        safe_photo_canvas_size,
    )
    from .message_sender import ImageMessageSender
    from .settings import (
        DEFAULT_ENABLE_COMPRESS_RETRY,
        DEFAULT_ENABLE_IMAGE_COMPRESSION,
        DEFAULT_RECALL_AFTER_SECONDS,
        LEGACY_DATA_FILE,
        LEGACY_TRIGGER_DATA_FILENAME,
        PLUGIN_NAME,
        PLUGIN_VERSION,
        TRIGGER_DATA_FILENAME,
        read_bool_config,
        read_non_negative_int_config,
    )
    from .trigger_store import (
        get_plugin_data_dir,
        is_http_url,
        load_trigger_map,
        migrate_legacy_trigger_data,
        normalize_trigger,
        save_trigger_map,
    )
except ImportError:
    if __package__:
        raise
    from image_files import (
        ImageFileService,
        content_type_to_extension,
        safe_photo_canvas_size,
    )
    from message_sender import ImageMessageSender
    from settings import (
        DEFAULT_ENABLE_COMPRESS_RETRY,
        DEFAULT_ENABLE_IMAGE_COMPRESSION,
        DEFAULT_RECALL_AFTER_SECONDS,
        LEGACY_DATA_FILE,
        LEGACY_TRIGGER_DATA_FILENAME,
        PLUGIN_NAME,
        PLUGIN_VERSION,
        TRIGGER_DATA_FILENAME,
        read_bool_config,
        read_non_negative_int_config,
    )
    from trigger_store import (
        get_plugin_data_dir,
        is_http_url,
        load_trigger_map,
        migrate_legacy_trigger_data,
        normalize_trigger,
        save_trigger_map,
    )

logger = logging.getLogger(__name__)


@register(PLUGIN_NAME, "djwlx", "自定义图片触发词插件，仅支持直接图片URL", PLUGIN_VERSION)
class DirectImageTriggerPlugin(Star):
    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.config = config or {}
        self.enable_image_compression = read_bool_config(
            self.config,
            "enable_image_compression",
            DEFAULT_ENABLE_IMAGE_COMPRESSION,
        )
        self.enable_compress_retry = read_bool_config(
            self.config,
            "enable_compress_retry",
            DEFAULT_ENABLE_COMPRESS_RETRY,
        )
        self.recall_after_seconds = read_non_negative_int_config(
            self.config,
            "recall_after_seconds",
            DEFAULT_RECALL_AFTER_SECONDS,
        )

        self.data_dir = get_plugin_data_dir()
        self.trigger_file = self.data_dir / TRIGGER_DATA_FILENAME
        self.temp_dir = self.data_dir / "temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        migrate_legacy_trigger_data(
            [
                self.data_dir / LEGACY_TRIGGER_DATA_FILENAME,
                LEGACY_DATA_FILE,
            ],
            self.trigger_file,
        )
        self.trigger_map: Dict[str, str] = load_trigger_map(self.trigger_file)

        self.image_files = ImageFileService(self.temp_dir)
        self.sender = ImageMessageSender(
            enable_compress_retry=self.enable_compress_retry,
            recall_after_seconds=self.recall_after_seconds,
            normalize_photo_dimensions=(
                lambda path: self._normalize_photo_dimensions(path)
            ),
        )

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

        image_path, cleanup_paths = await self._prepare_image_file(
            self.trigger_map[trigger_key]
        )
        if not image_path:
            await self._safe_send_plain(event, "获取图片失败，请检查图片 URL 是否有效")
            event.stop_event()
            return

        send_result = None
        try:
            send_result = await self._send_image_path(event, image_path)
        except Exception as exc:
            logger.warning("发送原图失败: %s", exc)
            send_result = await self._retry_compressed_image(
                event,
                image_path,
                cleanup_paths,
            )
            if send_result is None:
                await self._safe_send_plain(event, "发送图片失败，请稍后重试")

        event.stop_event()
        if cleanup_paths:
            self._schedule_cleanup(cleanup_paths)
        self._schedule_recall(event, send_result)

    async def terminate(self):
        self.sender.cancel_pending_recalls()

    async def _prepare_image_file(self, url: str) -> Tuple[Optional[str], List[str]]:
        image_path = await self._download_direct_image(url)
        if not image_path:
            return None, []

        cleanup_paths = [image_path]
        if not self.enable_image_compression:
            return image_path, cleanup_paths

        normalized_path = self._normalize_photo_dimensions(image_path)
        if normalized_path != image_path:
            cleanup_paths.append(normalized_path)

        return normalized_path, cleanup_paths

    async def _download_direct_image(self, url: str) -> Optional[str]:
        return await self.image_files.download_direct_image(url)

    def _normalize_photo_dimensions(self, image_path: str) -> str:
        return self.image_files.normalize_photo_dimensions(image_path)

    async def _send_image_path(self, event: AstrMessageEvent, image_path: str):
        return await self.sender.send_image_path(event, image_path)

    async def _safe_send_plain(self, event: AstrMessageEvent, text: str) -> None:
        await self.sender.safe_send_plain(event, text)

    async def _retry_compressed_image(
        self,
        event: AstrMessageEvent,
        image_path: str,
        cleanup_paths: List[str],
    ):
        return await self.sender.retry_compressed_image(event, image_path, cleanup_paths)

    def _schedule_cleanup(self, file_paths: List[str]):
        self.sender.schedule_cleanup(file_paths)

    def _schedule_recall(self, event: AstrMessageEvent, send_result) -> None:
        self.sender.schedule_recall(event, send_result)

    @staticmethod
    def _extract_sent_message_id(send_result) -> Optional[str]:
        return ImageMessageSender.extract_sent_message_id(send_result)

    async def _try_recall_message(
        self,
        event: AstrMessageEvent,
        message_id: str,
    ) -> bool:
        return await self.sender.try_recall_message(event, message_id)

    @staticmethod
    def _get_telegram_chat_id(event: AstrMessageEvent) -> Optional[str]:
        return ImageMessageSender.get_telegram_chat_id(event)
