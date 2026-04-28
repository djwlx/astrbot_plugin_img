import asyncio
import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


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

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain

    def stop_event(self):
        self.stopped = True


def install_astrbot_stubs():
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
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

    class Context:
        pass

    class Star:
        def __init__(self, context):
            self.context = context

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
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.register = register
    message_components_mod.Image = Image

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = astrbot_api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.message_components"] = message_components_mod


class DirectImageConfigTests(unittest.TestCase):
    def setUp(self):
        install_astrbot_stubs()
        sys.modules.pop("main", None)
        self.main = importlib.import_module("main")

    def test_normalize_trigger_strips_and_lowercases(self):
        self.assertEqual(self.main.normalize_trigger("  Cat  "), "cat")

    def test_is_http_url_accepts_only_http_and_https(self):
        self.assertTrue(self.main.is_http_url("https://example.com/cat.jpg"))
        self.assertTrue(self.main.is_http_url("http://example.com/cat.jpg"))
        self.assertFalse(self.main.is_http_url("ftp://example.com/cat.jpg"))
        self.assertFalse(self.main.is_http_url("example.com/cat.jpg"))

    def test_load_trigger_map_reads_only_string_url_entries(self):
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
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
            config_path = Path(temp_dir) / "config.json"

            self.main.save_trigger_map(
                {"cat": "https://example.com/cat.jpg"}, config_path
            )

            result = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"trigger_map": {"cat": "https://example.com/cat.jpg"}})

    def test_safe_photo_canvas_size_handles_telegram_photo_limits(self):
        normal = self.main.safe_photo_canvas_size(1200, 800)
        wide = self.main.safe_photo_canvas_size(4000, 100)
        large = self.main.safe_photo_canvas_size(8000, 4000)

        self.assertEqual(normal, (1200, 800))
        self.assertLessEqual(max(wide) / min(wide), 20)
        self.assertLessEqual(sum(large), 9900)

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

    def test_on_message_sends_prepared_local_image_file(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        plugin.trigger_map = {"cat": "https://example.com/cat.jpg"}
        cleanup_paths = []

        async def fake_prepare_image_file(url):
            self.assertEqual(url, "https://example.com/cat.jpg")
            return "/tmp/cat.jpg", ["/tmp/cat.jpg"]

        plugin._prepare_image_file = fake_prepare_image_file
        plugin._schedule_cleanup = cleanup_paths.extend
        event = ChainEvent("cat")

        results = asyncio.run(collect_async(plugin.on_message(event)))

        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 1)
        self.assertTrue(results[0][0].file.startswith("file:///"))
        self.assertEqual(results[0][0].path, "/tmp/cat.jpg")
        self.assertTrue(event.stopped)
        self.assertEqual(cleanup_paths, ["/tmp/cat.jpg"])


if __name__ == "__main__":
    unittest.main()
