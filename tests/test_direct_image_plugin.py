import asyncio
import errno
import importlib
import importlib.util
import io
import json
import logging
import os
import random
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image as PILImage


async def collect_async(async_iterable):
    results = []
    async for item in async_iterable:
        results.append(item)
    return results


class PlainEvent:
    def plain_result(self, text):
        return text


class ChainEvent:
    def __init__(self, message_str):
        self.message_str = message_str
        self.stopped = False
        self.sent_messages = []
        self.send_results = []
        self.send_errors = []

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain

    def stop_event(self):
        self.stopped = True

    async def send(self, message):
        self.sent_messages.append(message)
        if self.send_errors:
            raise self.send_errors.pop(0)
        if self.send_results:
            return self.send_results.pop(0)
        return None


def install_astrbot_stubs():
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_api.logger = logging.getLogger("astrbot")
    aiohttp_mod = types.ModuleType("aiohttp")
    event_mod = types.ModuleType("astrbot.api.event")
    star_mod = types.ModuleType("astrbot.api.star")
    message_components_mod = types.ModuleType("astrbot.api.message_components")

    class DummyFilter:
        class EventMessageType:
            ALL = "all"

        @staticmethod
        def command(_name):
            def decorator(func):
                return func
            return decorator

        @staticmethod
        def event_message_type(_message_type):
            def decorator(func):
                return func
            return decorator

    class AstrMessageEvent:
        pass

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = chain or []

    class Context:
        pass

    class Star:
        def __init__(self, context):
            self.context = context

    class StarTools:
        @staticmethod
        def get_data_dir(plugin_name=None):
            base = os.environ.get("ASTRBOT_PLUGIN_TEST_DATA_DIR")
            if not base:
                base = os.path.join("/tmp", "astrbot_plugin_img_test_data")
            path = Path(base) / (plugin_name or "unknown")
            path.mkdir(parents=True, exist_ok=True)
            return path

    def register(*_args, **_kwargs):
        def decorator(cls):
            return cls
        return decorator

    class Image:
        def __init__(self, file):
            self.file = file
            self.path = ""

        @staticmethod
        def fromFileSystem(path, **_):
            image = Image(file=f"file:///{os.path.abspath(path)}")
            image.path = path
            return image

    class Plain:
        def __init__(self, text):
            self.text = text

    class ClientSession:
        pass

    class ClientTimeout:
        def __init__(self, total=None):
            self.total = total

    aiohttp_mod.ClientSession = ClientSession
    aiohttp_mod.ClientTimeout = ClientTimeout

    sys.modules["aiohttp"] = aiohttp_mod
    event_mod.filter = DummyFilter
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.StarTools = StarTools
    star_mod.register = register
    message_components_mod.Image = Image
    message_components_mod.Plain = Plain

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.message_components"] = message_components_mod


class DirectImageConfigTests(unittest.TestCase):
    def setUp(self):
        install_astrbot_stubs()
        for module_name in (
            "main",
            "trigger_store",
        ):
            sys.modules.pop(module_name, None)
        self.main = importlib.import_module("main")

    def test_normalize_trigger_strips_and_lowercases(self):
        self.assertEqual(self.main.normalize_trigger("  Cat  "), "cat")
        self.assertEqual(self.main.normalize_trigger(None), "")

    def test_is_http_url_accepts_only_http_and_https(self):
        self.assertTrue(self.main.is_http_url("https://example.com/cat.jpg"))
        self.assertTrue(self.main.is_http_url("http://example.com/cat.jpg"))
        self.assertFalse(self.main.is_http_url("ftp://example.com/cat.jpg"))
        self.assertFalse(self.main.is_http_url("example.com/cat.jpg"))

    def test_load_trigger_map_reads_only_string_url_entries(self):
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "triggers.json"
            config_path.write_text(
                json.dumps(
                    {
                        "trigger_map": {
                            "Cat": "https://example.com/cat.jpg",
                            "old": {
                                "type": "url",
                                "value": "https://example.com/old.jpg",
                            },
                            "bad": 123,
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = self.main.load_trigger_map(config_path)

        self.assertEqual(result, {"cat": "https://example.com/cat.jpg"})

    def test_save_trigger_map_writes_minimal_config_shape(self):
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "triggers.json"

            self.main.save_trigger_map(
                {"cat": "https://example.com/cat.jpg"}, config_path
            )

            result = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"trigger_map": {"cat": "https://example.com/cat.jpg"}})

    def test_plugin_starts_empty_when_only_legacy_config_exists(self):
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "astrbot_plugin_img"
            data_dir.mkdir()
            legacy_path = data_dir / "config.json"
            legacy_path.write_text(
                json.dumps(
                    {"trigger_map": {"Cat": "https://example.com/cat.jpg"}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"ASTRBOT_PLUGIN_TEST_DATA_DIR": temp_dir}):
                plugin = self.main.DirectImageTriggerPlugin(object())

            self.assertEqual(plugin.trigger_map, {})
            self.assertFalse(plugin.trigger_file.exists())
            self.assertTrue(legacy_path.exists())

    def test_plugin_uses_astrbot_plugin_data_directory_for_runtime_files(self):
        with TemporaryDirectory() as temp_dir:
            os.environ["ASTRBOT_PLUGIN_TEST_DATA_DIR"] = temp_dir
            try:
                plugin = self.main.DirectImageTriggerPlugin(object())
            finally:
                os.environ.pop("ASTRBOT_PLUGIN_TEST_DATA_DIR", None)

            self.assertEqual(
                plugin.trigger_file,
                Path(temp_dir) / "astrbot_plugin_img" / "triggers.json",
            )
            self.assertEqual(
                plugin.temp_dir,
                Path(temp_dir) / "astrbot_plugin_img" / "temp",
            )

    def test_list_image_triggers_returns_sorted_trigger_words_without_urls(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {
            "dog": "https://example.com/dog.jpg",
            "cat": "https://example.com/cat.jpg",
        }

        results = asyncio.run(collect_async(plugin.list_image_triggers(PlainEvent())))

        self.assertEqual(results, ["图片触发词列表:\ncat\ndog"])

    def test_list_image_triggers_handles_empty_map(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {}

        results = asyncio.run(collect_async(plugin.list_image_triggers(PlainEvent())))

        self.assertEqual(results, ["暂无图片触发词"])

    def test_download_logs_outcome_without_exposing_url_or_exception_text(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        url = "https://example.com/image?token=secret-token"
        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            for status, error, expected in (
                (200, None, "获取图片成功"),
                (403, None, "HTTP 状态码=403"),
                (200, RuntimeError(f"failed: {url}\nprivate-message"), "RuntimeError"),
                (200, asyncio.TimeoutError(), "下载超时（20秒）"),
            ):
                response = MagicMock()
                response.status = status
                response.headers = {"Content-Type": "application/octet-stream"}
                response.read = AsyncMock(return_value=b"image-bytes", side_effect=error)
                session = MagicMock()
                session.__aenter__.return_value = session
                session.get.return_value.__aenter__.return_value = response
                with (
                    self.subTest(status=status, error=type(error).__name__),
                    patch.object(self.main.aiohttp, "ClientSession", return_value=session),
                    self.assertLogs(self.main.logger, level="INFO") as logs,
                ):
                    result = asyncio.run(plugin._download_direct_image(url))

                logged = "\n".join(logs.output)
                self.assertTrue(all(record.name == "astrbot" for record in logs.records))
                self.assertIn(expected, logged)
                self.assertNotIn(url, logged)
                self.assertNotIn("secret-token", logged)
                self.assertNotIn("private-message", logged)
                self.assertEqual(session.get.call_args.kwargs["timeout"].total, 20)
                if status == 200 and error is None:
                    self.assertEqual(Path(result).read_bytes(), b"image-bytes")
                else:
                    self.assertIsNone(result)

    def test_on_message_sends_downloaded_original_image(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.jpg"}
        event = ChainEvent("  Cat  ")

        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            image_path = Path(temp_dir) / "original.jpg"
            image_path.write_bytes(b"original-image-bytes")
            unrelated_path = Path(temp_dir) / "previous-failure.jpg"
            unrelated_path.write_bytes(b"keep-this-file")

            original_send = event.send

            async def checked_send(message):
                self.assertEqual(image_path.read_bytes(), b"original-image-bytes")
                await original_send(message)

            event.send = checked_send

            async def fake_download(url):
                self.assertEqual(url, "https://example.com/cat.jpg")
                return str(image_path)

            plugin._download_direct_image = fake_download
            with self.assertLogs(self.main.logger, level="INFO") as logs:
                asyncio.run(plugin.on_message(event))

            logged = "\n".join(logs.output)
            self.assertIn("图片无需压缩", logged)
            self.assertIn("发送图片成功", logged)
            self.assertIn("图片临时文件清理完成", logged)

            self.assertEqual(len(event.sent_messages), 1)
            self.assertEqual(event.sent_messages[0].chain[0].path, str(image_path))
            self.assertFalse(image_path.exists())
            self.assertEqual(unrelated_path.read_bytes(), b"keep-this-file")
        self.assertTrue(event.stopped)

    def test_on_message_reports_download_failure(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.jpg"}
        event = ChainEvent("cat")

        async def fake_download(_url):
            return None

        plugin._download_direct_image = fake_download
        asyncio.run(plugin.on_message(event))

        self.assertEqual(len(event.sent_messages), 1)
        self.assertEqual(
            event.sent_messages[0].chain[0].text,
            "获取图片失败，请检查图片 URL 是否有效",
        )
        self.assertTrue(event.stopped)

    def test_on_message_reports_send_failure(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.jpg"}
        event = ChainEvent("cat")
        event.send_errors.append(RuntimeError("send failed: secret-token private-message"))

        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            image_path = Path(temp_dir) / "original.jpg"
            image_path.write_bytes(b"original-image-bytes")

            async def fake_download(_url):
                return str(image_path)

            plugin._download_direct_image = fake_download
            with self.assertLogs(self.main.logger, level="INFO") as logs:
                asyncio.run(plugin.on_message(event))

            logged = "\n".join(logs.output)
            self.assertIn("发送图片失败", logged)
            self.assertIn("RuntimeError", logged)
            self.assertNotIn("发送图片成功", logged)
            self.assertNotIn("secret-token", logged)
            self.assertNotIn("private-message", logged)

            self.assertEqual(len(event.sent_messages), 2)
            self.assertEqual(event.sent_messages[0].chain[0].path, str(image_path))
            self.assertEqual(image_path.read_bytes(), b"original-image-bytes")
        self.assertEqual(
            event.sent_messages[1].chain[0].text,
            "发送图片失败，请稍后重试",
        )
        self.assertTrue(event.stopped)

    def test_image_at_size_limit_is_kept_unchanged(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        with TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "original.png"
            with PILImage.new("RGB", (8, 8), "red") as image:
                image.save(image_path)
            with image_path.open("ab") as image_file:
                image_file.truncate(self.main.MAX_IMAGE_BYTES)

            result = plugin._compress_image_to_webp(str(image_path))

            self.assertEqual(result, str(image_path))
            self.assertEqual(image_path.stat().st_size, 4_000_000)

    def test_large_transparent_image_is_sent_as_webp_under_four_mb(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.png"}
        event = ChainEvent("cat")
        sent_data = []
        original_send = event.send

        async def capture_send(message):
            sent_data.append(Path(message.chain[0].path).read_bytes())
            await original_send(message)

        event.send = capture_send
        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            source_path = plugin.temp_dir / "original.png"
            pixels = random.Random(7).randbytes(1200 * 1000 * 3)
            with PILImage.frombytes("RGB", (1200, 1000), pixels) as image:
                image.putalpha(128)
                image.save(source_path, compress_level=0)
            self.assertGreater(source_path.stat().st_size, 4_000_000)

            async def fake_download(_url):
                return str(source_path)

            plugin._download_direct_image = fake_download
            asyncio.run(plugin.on_message(event))

            self.assertEqual(len(event.sent_messages), 1)
            sent_path = Path(event.sent_messages[0].chain[0].path)
            self.assertEqual(sent_path.suffix, ".webp")
            self.assertLessEqual(len(sent_data[0]), 4_000_000)
            with PILImage.open(io.BytesIO(sent_data[0])) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertEqual(image.size, (1200, 1000))
                self.assertEqual(image.getchannel("A").getextrema(), (128, 128))
            self.assertFalse(source_path.exists())
            self.assertFalse(sent_path.exists())
        self.assertTrue(event.stopped)

    def test_oversized_webp_is_resized_when_quality_reduction_is_insufficient(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            source_path = plugin.temp_dir / "original.webp"
            pixels = random.Random(8).randbytes(160 * 120 * 3)
            with PILImage.frombytes("RGB", (160, 120), pixels) as image:
                image.save(source_path, lossless=True)

            with patch.object(self.main, "MAX_IMAGE_BYTES", 1000):
                result = plugin._compress_image_to_webp(str(source_path))

            self.assertNotEqual(result, str(source_path))
            self.assertLessEqual(Path(result).stat().st_size, 1000)
            with PILImage.open(result) as image:
                self.assertLess(image.width, 160)
                self.assertLess(image.height, 120)

    def test_gif_compression_preserves_animation_timing_and_loop(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            source_path = plugin.temp_dir / "original.gif"
            frames = [
                PILImage.new("RGB", (64, 64), color)
                for color in ("red", "green", "blue")
            ]
            try:
                frames[0].save(
                    source_path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=[80, 120, 160],
                    loop=2,
                )
            finally:
                for frame in frames:
                    frame.close()
            with source_path.open("ab") as image_file:
                image_file.truncate(1001)

            with patch.object(self.main, "MAX_IMAGE_BYTES", 1000):
                result = plugin._compress_image_to_webp(str(source_path))

            self.assertLessEqual(Path(result).stat().st_size, 1000)
            with PILImage.open(result) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertEqual(image.n_frames, 3)
                self.assertEqual(image.info["loop"], 2)
                durations = []
                for index in range(image.n_frames):
                    image.seek(index)
                    image.load()
                    durations.append(image.info["duration"])
                self.assertEqual(durations, [80, 120, 160])

    def test_compression_failure_does_not_send_oversized_original(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.png"}
        event = ChainEvent("cat")
        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            source_path = Path(temp_dir) / "invalid.png"
            with source_path.open("wb") as image_file:
                image_file.truncate(self.main.MAX_IMAGE_BYTES + 1)

            async def fake_download(_url):
                return str(source_path)

            plugin._download_direct_image = fake_download
            asyncio.run(plugin.on_message(event))

            self.assertEqual(len(event.sent_messages), 1)
            self.assertIn("图片压缩失败", event.sent_messages[0].chain[0].text)
            self.assertIn("格式无法识别或文件已损坏", event.sent_messages[0].chain[0].text)
            self.assertTrue(source_path.exists())
        self.assertTrue(event.stopped)

    def test_compression_accepts_over_forty_million_pixels_with_pillow_limit_disabled(self):
        with patch.object(PILImage, "MAX_IMAGE_PIXELS", 100):
            importlib.reload(self.main)
            plugin = self.main.DirectImageTriggerPlugin(object())
            with TemporaryDirectory() as temp_dir:
                plugin.temp_dir = Path(temp_dir)
                source_path = plugin.temp_dir / "large.png"
                with PILImage.new("L", (6400, 6400), 128) as image:
                    image.save(source_path)
                with source_path.open("ab") as image_file:
                    image_file.truncate(self.main.MAX_IMAGE_BYTES + 1)

                result = plugin._compress_image_to_webp(str(source_path))

                self.assertLessEqual(Path(result).stat().st_size, self.main.MAX_IMAGE_BYTES)
                with PILImage.open(result) as image:
                    self.assertEqual(image.format, "WEBP")
                    self.assertEqual(image.size, (6400, 6400))

    def test_compression_preserves_animation_with_more_than_three_hundred_frames(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            source_path = plugin.temp_dir / "original.gif"
            frames = [
                PILImage.new("RGB", (8, 8), "red" if index % 2 else "blue")
                for index in range(301)
            ]
            try:
                frames[0].save(
                    source_path, save_all=True, append_images=frames[1:], duration=100
                )
            finally:
                for frame in frames:
                    frame.close()

            with source_path.open("ab") as image_file:
                image_file.truncate(self.main.MAX_IMAGE_BYTES + 1)

            result = plugin._compress_image_to_webp(str(source_path))

            self.assertLessEqual(Path(result).stat().st_size, self.main.MAX_IMAGE_BYTES)
            with PILImage.open(result) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertEqual(image.n_frames, 301)

    def test_missing_webp_encoder_has_specific_error(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "original.png"
            with PILImage.new("RGB", (8, 8), "red") as image:
                image.save(source_path)
            with (
                patch.object(self.main, "MAX_IMAGE_BYTES", 1),
                patch.object(self.main.features, "check", return_value=False),
                self.assertRaisesRegex(self.main.ImageCompressionError, "缺少 WebP"),
            ):
                plugin._compress_image_to_webp(str(source_path))

    def test_processing_errors_report_reason_and_preserve_source(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.png"}
        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            source_path = plugin.temp_dir / "original.png"
            source_path.write_bytes(b"keep-original")

            async def fake_download(_url):
                return str(source_path)

            plugin._download_direct_image = fake_download
            for error, expected in (
                (MemoryError(), "内存不足"),
                (PermissionError(), "没有读写权限"),
                (OSError(errno.ENOSPC, "no space left"), "磁盘空间不足"),
            ):
                event = ChainEvent("cat")
                with (
                    self.subTest(error=type(error).__name__),
                    patch.object(plugin, "_compress_image_to_webp", side_effect=error),
                    self.assertLogs(self.main.logger, level="WARNING") as logs,
                ):
                    asyncio.run(plugin.on_message(event))

                self.assertEqual(len(event.sent_messages), 1)
                self.assertIn(expected, event.sent_messages[0].chain[0].text)
                self.assertIn(expected, logs.output[0])
                self.assertTrue(event.stopped)
                self.assertEqual(source_path.read_bytes(), b"keep-original")

    def test_failed_send_preserves_original_and_compressed_image(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.png"}
        event = ChainEvent("cat")
        event.send_errors.append(RuntimeError("send failed"))
        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            source_path = plugin.temp_dir / "original.png"
            pixels = random.Random(9).randbytes(160 * 120 * 3)
            with PILImage.frombytes("RGB", (160, 120), pixels) as image:
                image.save(source_path)

            async def fake_download(_url):
                return str(source_path)

            plugin._download_direct_image = fake_download
            with patch.object(self.main, "MAX_IMAGE_BYTES", 1000):
                asyncio.run(plugin.on_message(event))

            sent_path = Path(event.sent_messages[0].chain[0].path)
            self.assertEqual(sent_path.suffix, ".webp")
            self.assertTrue(source_path.exists())
            self.assertTrue(sent_path.exists())
            self.assertEqual(len(event.sent_messages), 2)
        self.assertTrue(event.stopped)

    def test_cleanup_failure_does_not_report_send_failure(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.jpg"}
        event = ChainEvent("cat")
        with TemporaryDirectory() as temp_dir:
            plugin.temp_dir = Path(temp_dir)
            source_path = plugin.temp_dir / "original.jpg"
            source_path.write_bytes(b"original-image-bytes")

            async def fake_download(_url):
                return str(source_path)

            plugin._download_direct_image = fake_download
            with (
                patch.object(Path, "unlink", side_effect=PermissionError),
                self.assertLogs(self.main.logger, level="WARNING") as logs,
            ):
                asyncio.run(plugin.on_message(event))

            self.assertEqual(len(event.sent_messages), 1)
            self.assertTrue(source_path.exists())
            self.assertIn("清理图片临时文件失败", logs.output[0])
        self.assertTrue(event.stopped)

    def test_cleanup_skips_files_outside_temp_directory_and_symlinks(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        with TemporaryDirectory() as temp_dir, TemporaryDirectory() as outside_dir:
            plugin.temp_dir = Path(temp_dir)
            outside_path = Path(outside_dir) / "keep.jpg"
            outside_path.write_bytes(b"keep-this-file")
            symlink_path = plugin.temp_dir / "link.jpg"
            symlink_path.symlink_to(outside_path)

            with self.assertLogs(self.main.logger, level="WARNING"):
                plugin._cleanup_image_files(str(outside_path), str(symlink_path))

            self.assertEqual(outside_path.read_bytes(), b"keep-this-file")
            self.assertTrue(symlink_path.is_symlink())

    def test_send_rejects_oversized_file_even_if_compression_was_bypassed(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        event = ChainEvent("cat")
        with TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "oversized.webp"
            with image_path.open("wb") as image_file:
                image_file.truncate(self.main.MAX_IMAGE_BYTES + 1)

            with self.assertRaises(ValueError):
                asyncio.run(plugin._send_image_path(event, str(image_path)))

        self.assertEqual(event.sent_messages, [])

    def test_on_message_ignores_non_text_message_str(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.jpg"}
        event = ChainEvent(None)

        asyncio.run(plugin.on_message(event))

        self.assertFalse(event.stopped)
        self.assertEqual(event.sent_messages, [])

    def test_changelog_contains_metadata_version(self):
        repo_root = Path(__file__).resolve().parents[1]
        metadata = (repo_root / "metadata.yaml").read_text(encoding="utf-8")
        version_line = next(
            line for line in metadata.splitlines() if line.startswith("version:")
        )
        version = version_line.split(":", 1)[1].split("#", 1)[0].strip()

        changelog = (repo_root / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertIn(f"## {version}", changelog)

    def test_main_imports_when_loaded_as_astrbot_plugin_package(self):
        repo_root = Path(__file__).resolve().parents[1]
        package_names = [
            "data",
            "data.plugins",
            "data.plugins.astrbot_plugin_img",
        ]
        module_names = package_names + [
            "main",
            "trigger_store",
            "data.plugins.astrbot_plugin_img.main",
            "data.plugins.astrbot_plugin_img.trigger_store",
        ]
        old_modules = {name: sys.modules.get(name) for name in module_names}
        old_sys_path = list(sys.path)

        try:
            for name in module_names:
                sys.modules.pop(name, None)

            data_pkg = types.ModuleType("data")
            data_pkg.__path__ = []
            plugins_pkg = types.ModuleType("data.plugins")
            plugins_pkg.__path__ = []
            plugin_pkg = types.ModuleType("data.plugins.astrbot_plugin_img")
            plugin_pkg.__path__ = [str(repo_root)]
            sys.modules["data"] = data_pkg
            sys.modules["data.plugins"] = plugins_pkg
            sys.modules["data.plugins.astrbot_plugin_img"] = plugin_pkg
            sys.path = [
                path
                for path in sys.path
                if path not in ("", str(repo_root), os.getcwd())
            ]

            spec = importlib.util.spec_from_file_location(
                "data.plugins.astrbot_plugin_img.main",
                repo_root / "main.py",
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            self.assertTrue(hasattr(module, "DirectImageTriggerPlugin"))
        finally:
            sys.path = old_sys_path
            for name in module_names:
                sys.modules.pop(name, None)
            for name, module in old_modules.items():
                if module is not None:
                    sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
