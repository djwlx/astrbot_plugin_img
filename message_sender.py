import asyncio
import base64
import logging
import os
from typing import Callable, List, Optional

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain

logger = logging.getLogger(__name__)


class ImageMessageSender:
    def __init__(
        self,
        enable_compress_retry: bool,
        recall_after_seconds: int,
        normalize_photo_dimensions: Callable[[str], str],
    ):
        self.enable_compress_retry = enable_compress_retry
        self.recall_after_seconds = recall_after_seconds
        self.normalize_photo_dimensions = normalize_photo_dimensions
        self.recall_tasks: List[asyncio.Task] = []

    async def send_image_path(self, event: AstrMessageEvent, image_path: str):
        if self.recall_after_seconds > 0 and self._supports_onebot_send(event):
            result = await self._send_onebot_image(event, image_path)
            self._mark_event_sent(event)
            return result

        if self.recall_after_seconds > 0 and self._supports_telegram_send(event):
            result = await self._send_telegram_image(event, image_path)
            self._mark_event_sent(event)
            return result

        result = await event.send(MessageChain([Image.fromFileSystem(image_path)]))
        return result if result is not None else True

    async def safe_send_plain(self, event: AstrMessageEvent, text: str) -> None:
        try:
            await event.send(MessageChain([Plain(text)]))
        except Exception as exc:
            logger.error("发送文本提示失败: %s", exc)

    async def retry_compressed_image(
        self,
        event: AstrMessageEvent,
        image_path: str,
        cleanup_paths: List[str],
    ):
        if not self.enable_compress_retry:
            return None

        retry_path = self.normalize_photo_dimensions(cleanup_paths[0])
        if retry_path not in cleanup_paths:
            cleanup_paths.append(retry_path)
        if retry_path == image_path:
            return None

        try:
            return await self.send_image_path(event, retry_path)
        except Exception as exc:
            logger.error("压缩后重试发送失败: %s", exc)
            return None

    def schedule_cleanup(self, file_paths: List[str]):
        async def delayed_cleanup():
            await asyncio.sleep(60)
            for file_path in file_paths:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as exc:
                    logger.error("清理临时文件失败: %s", exc)

        asyncio.create_task(delayed_cleanup())

    def schedule_recall(self, event: AstrMessageEvent, send_result) -> None:
        if self.recall_after_seconds <= 0:
            return
        message_id = self.extract_sent_message_id(send_result)
        if not message_id:
            logger.warning("无法撤回图片：平台未返回已发送消息 ID")
            return

        async def delayed_recall():
            await asyncio.sleep(self.recall_after_seconds)
            await self.try_recall_message(event, message_id)

        task = asyncio.create_task(delayed_recall())
        task.add_done_callback(self._remove_recall_task)
        self.recall_tasks.append(task)

    def cancel_pending_recalls(self) -> None:
        for task in list(self.recall_tasks):
            task.cancel()
        self.recall_tasks.clear()

    @staticmethod
    def extract_sent_message_id(send_result) -> Optional[str]:
        if send_result is None:
            return None
        if isinstance(send_result, bool):
            return None
        if isinstance(send_result, dict):
            value = ImageMessageSender._extract_message_id_from_dict(send_result)
            if value:
                return value
        for attr in ("message_id", "msg_id", "id"):
            value = getattr(send_result, attr, None)
            if value:
                return str(value)
        return None

    async def try_recall_message(
        self,
        event: AstrMessageEvent,
        message_id: str,
    ) -> bool:
        for method_name in ("recall_message", "revoke_message", "delete_message"):
            method = getattr(event, method_name, None)
            if callable(method):
                try:
                    await method(message_id)
                    return True
                except TypeError:
                    try:
                        await method()
                        return True
                    except Exception:
                        continue
                except Exception as exc:
                    logger.warning("撤回消息失败: %s", exc)
                    return False

        bot = getattr(event, "bot", None)
        delete_msg = getattr(bot, "delete_msg", None)
        if callable(delete_msg):
            try:
                await delete_msg(message_id=int(message_id))
                return True
            except Exception as exc:
                logger.warning("OneBot 撤回消息失败: %s", exc)
                return False

        client = getattr(event, "client", None)
        delete_message = getattr(client, "delete_message", None)
        if callable(delete_message):
            chat_id = self.get_telegram_chat_id(event)
            if chat_id:
                try:
                    await delete_message(chat_id=chat_id, message_id=int(message_id))
                    return True
                except Exception as exc:
                    logger.warning("Telegram 撤回消息失败: %s", exc)
                    return False

        logger.warning("当前平台不支持插件侧撤回消息")
        return False

    async def _send_onebot_image(self, event: AstrMessageEvent, image_path: str):
        bot = getattr(event, "bot", None)
        message = [self._onebot_image_segment(image_path)]
        group_id = self._integer_id(self._call_event_method(event, "get_group_id"))
        if group_id is not None:
            return await bot.send_group_msg(group_id=group_id, message=message)

        user_id = self._integer_id(self._call_event_method(event, "get_sender_id"))
        if user_id is not None:
            return await bot.send_private_msg(user_id=user_id, message=message)

        raw_event = getattr(getattr(event, "message_obj", None), "raw_message", None)
        send = getattr(bot, "send", None)
        if callable(send) and raw_event is not None:
            return await send(event=raw_event, message=message)

        raise ValueError("无法通过 OneBot 发送图片：缺少有效群号或用户 ID")

    async def _send_telegram_image(self, event: AstrMessageEvent, image_path: str):
        client = getattr(event, "client", None)
        payload = self._telegram_payload(event)
        if self._is_gif(image_path) and hasattr(client, "send_animation"):
            return await client.send_animation(animation=image_path, **payload)
        return await client.send_photo(photo=image_path, **payload)

    @staticmethod
    def _onebot_image_segment(image_path: str) -> dict:
        with open(image_path, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("utf-8")
        return {"type": "image", "data": {"file": f"base64://{encoded}"}}

    @staticmethod
    def _extract_message_id_from_dict(send_result: dict) -> Optional[str]:
        for key in ("message_id", "msg_id", "id"):
            value = send_result.get(key)
            if value:
                return str(value)

        data = send_result.get("data")
        if isinstance(data, dict):
            for key in ("message_id", "msg_id", "id"):
                value = data.get(key)
                if value:
                    return str(value)
        return None

    @staticmethod
    def _supports_onebot_send(event: AstrMessageEvent) -> bool:
        bot = getattr(event, "bot", None)
        return bool(
            bot
            and callable(getattr(bot, "send_group_msg", None))
            and callable(getattr(bot, "send_private_msg", None))
        )

    @staticmethod
    def _supports_telegram_send(event: AstrMessageEvent) -> bool:
        client = getattr(event, "client", None)
        return bool(client and callable(getattr(client, "send_photo", None)))

    @staticmethod
    def _call_event_method(event: AstrMessageEvent, method_name: str):
        method = getattr(event, method_name, None)
        if callable(method):
            return method()
        return None

    @staticmethod
    def _integer_id(value) -> Optional[int]:
        if value is None:
            return None
        clean_value = str(value).split("#", 1)[0]
        if not clean_value.isdigit():
            return None
        return int(clean_value)

    @staticmethod
    def _telegram_payload(event: AstrMessageEvent) -> dict:
        message_obj = getattr(event, "message_obj", None)
        chat_id = getattr(message_obj, "group_id", None)
        message_thread_id = None
        if chat_id:
            chat_id = str(chat_id)
            if "#" in chat_id:
                chat_id, message_thread_id = chat_id.split("#", 1)
        else:
            chat_id = ImageMessageSender._call_event_method(event, "get_sender_id")

        payload = {"chat_id": chat_id}
        if message_thread_id:
            payload["message_thread_id"] = message_thread_id
        return payload

    @staticmethod
    def get_telegram_chat_id(event: AstrMessageEvent) -> Optional[str]:
        message_obj = getattr(event, "message_obj", None)
        group_id = getattr(message_obj, "group_id", None)
        if group_id:
            return str(group_id).split("#", 1)[0]
        sender_id = getattr(event, "get_sender_id", None)
        if callable(sender_id):
            return str(sender_id())
        return getattr(message_obj, "session_id", None)

    @staticmethod
    def _is_gif(path: str) -> bool:
        if path.lower().endswith(".gif"):
            return True
        try:
            with open(path, "rb") as f:
                return f.read(6) in (b"GIF87a", b"GIF89a")
        except OSError:
            return False

    @staticmethod
    def _mark_event_sent(event: AstrMessageEvent) -> None:
        try:
            setattr(event, "_has_send_oper", True)
        except Exception:
            pass

    def _remove_recall_task(self, task: asyncio.Task) -> None:
        try:
            self.recall_tasks.remove(task)
        except ValueError:
            pass
