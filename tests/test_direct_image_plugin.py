import asyncio
import importlib
import importlib.util
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


class OneBot:
    def __init__(self):
        self.group_messages = []
        self.private_messages = []
        self.deleted_messages = []

    async def send_group_msg(self, **kwargs):
        self.group_messages.append(kwargs)
        return {"message_id": 321}

    async def send_private_msg(self, **kwargs):
        self.private_messages.append(kwargs)
        return {"message_id": 654}

    async def delete_msg(self, **kwargs):
        self.deleted_messages.append(kwargs)


class OneBotEvent(ChainEvent):
    def __init__(self, message_str, group_id="10001", sender_id="20002"):
        super().__init__(message_str)
        self.bot = OneBot()
        self._group_id = group_id
        self._sender_id = sender_id

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    async def send(self, _message):
        raise AssertionError("OneBot recallable sends should use bot APIs directly")


class TelegramClient:
    def __init__(self):
        self.photos = []
        self.deleted_messages = []

    async def send_photo(self, **kwargs):
        self.photos.append(kwargs)
        return types.SimpleNamespace(message_id=777)

    async def delete_message(self, **kwargs):
        self.deleted_messages.append(kwargs)


class TelegramEvent(ChainEvent):
    def __init__(self, message_str, group_id="12345#678"):
        super().__init__(message_str)
        self.client = TelegramClient()
        self.message_obj = types.SimpleNamespace(group_id=group_id, session_id="999")

    def get_sender_id(self):
        return "999"

    async def send(self, _message):
        raise AssertionError("Telegram recallable sends should use client APIs directly")


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
            "settings",
            "trigger_store",
            "image_files",
            "message_sender",
        ):
            sys.modules.pop(module_name, None)
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

    def test_migrate_legacy_trigger_data_copies_old_plugin_root_config(self):
        with TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / "legacy_config.json"
            trigger_path = Path(temp_dir) / "plugin_data" / "triggers.json"
            legacy_path.write_text(
                json.dumps(
                    {"trigger_map": {"Cat": "https://example.com/cat.jpg"}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            migrated = self.main.migrate_legacy_trigger_data(
                [legacy_path],
                trigger_path,
            )

            result = json.loads(trigger_path.read_text(encoding="utf-8"))

        self.assertTrue(migrated)
        self.assertEqual(result, {"trigger_map": {"cat": "https://example.com/cat.jpg"}})

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

    def test_plugin_reads_runtime_options_from_astrbot_config(self):
        plugin = self.main.DirectImageTriggerPlugin(
            object(),
            config={
                "enable_image_compression": False,
                "enable_compress_retry": False,
                "recall_after_seconds": 8,
            },
        )

        self.assertFalse(plugin.enable_image_compression)
        self.assertFalse(plugin.enable_compress_retry)
        self.assertEqual(plugin.recall_after_seconds, 8)

    def test_schedule_recall_skips_when_recall_delay_is_zero(self):
        plugin = self.main.DirectImageTriggerPlugin(
            object(), config={"recall_after_seconds": 0}
        )
        event = ChainEvent("cat")

        async def fail_recall(_event, _message_id):
            raise AssertionError("recall should be disabled")

        plugin._try_recall_message = fail_recall

        plugin._schedule_recall(event, {"message_id": "123"})

    def test_extract_sent_message_id_supports_common_result_shapes(self):
        class SendResult:
            message_id = 456

        plugin = self.main.DirectImageTriggerPlugin(object())

        self.assertEqual(plugin._extract_sent_message_id({"msg_id": "123"}), "123")
        self.assertEqual(plugin._extract_sent_message_id(SendResult()), "456")
        self.assertIsNone(plugin._extract_sent_message_id(True))

    def test_send_image_path_uses_onebot_api_when_recall_is_enabled(self):
        plugin = self.main.DirectImageTriggerPlugin(
            object(), config={"recall_after_seconds": 5}
        )
        event = OneBotEvent("cat")

        with TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "cat.jpg"
            image_path.write_bytes(b"image-bytes")

            send_result = asyncio.run(plugin._send_image_path(event, str(image_path)))

        self.assertEqual(send_result, {"message_id": 321})
        self.assertEqual(event.bot.group_messages[0]["group_id"], 10001)
        image_segment = event.bot.group_messages[0]["message"][0]
        self.assertEqual(image_segment["type"], "image")
        self.assertTrue(image_segment["data"]["file"].startswith("base64://"))

    def test_send_image_path_uses_telegram_client_when_recall_is_enabled(self):
        plugin = self.main.DirectImageTriggerPlugin(
            object(), config={"recall_after_seconds": 5}
        )
        event = TelegramEvent("cat")

        with TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "cat.jpg"
            image_path.write_bytes(b"image-bytes")

            send_result = asyncio.run(plugin._send_image_path(event, str(image_path)))

        self.assertEqual(send_result.message_id, 777)
        self.assertEqual(
            event.client.photos,
            [
                {
                    "photo": str(image_path),
                    "chat_id": "12345",
                    "message_thread_id": "678",
                }
            ],
        )

    def test_try_recall_message_uses_onebot_delete_msg(self):
        plugin = self.main.DirectImageTriggerPlugin(object())
        event = OneBotEvent("cat")

        result = asyncio.run(plugin._try_recall_message(event, "321"))

        self.assertTrue(result)
        self.assertEqual(event.bot.deleted_messages, [{"message_id": 321}])

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

    def test_prepare_image_file_skips_compression_when_disabled(self):
        plugin = self.main.DirectImageTriggerPlugin(
            object(), config={"enable_image_compression": False}
        )

        async def fake_download(_url):
            return "/tmp/original.jpg"

        def fail_normalize(_path):
            raise AssertionError("compression should be disabled")

        plugin._download_direct_image = fake_download
        plugin._normalize_photo_dimensions = fail_normalize

        image_path, cleanup_paths = asyncio.run(
            plugin._prepare_image_file("https://example.com/cat.jpg")
        )

        self.assertEqual(image_path, "/tmp/original.jpg")
        self.assertEqual(cleanup_paths, ["/tmp/original.jpg"])

    def test_on_message_retries_with_compressed_image_after_send_failure(self):
        plugin = self.main.DirectImageTriggerPlugin(
            object(),
            config={
                "enable_image_compression": False,
                "enable_compress_retry": True,
            },
        )
        plugin.trigger_map = {"cat": "https://example.com/cat.jpg"}
        cleanup_paths = []

        async def fake_download(url):
            self.assertEqual(url, "https://example.com/cat.jpg")
            return "/tmp/original.jpg"

        plugin._download_direct_image = fake_download
        plugin._normalize_photo_dimensions = lambda path: "/tmp/compressed.jpg"
        plugin._schedule_cleanup = cleanup_paths.extend
        event = ChainEvent("cat")
        event.send_errors.append(RuntimeError("send failed"))

        asyncio.run(plugin.on_message(event))

        sent_paths = [message.chain[0].path for message in event.sent_messages]
        self.assertEqual(sent_paths, ["/tmp/original.jpg", "/tmp/compressed.jpg"])
        self.assertTrue(event.stopped)
        self.assertEqual(cleanup_paths, ["/tmp/original.jpg", "/tmp/compressed.jpg"])

    def test_conf_schema_defines_runtime_options(self):
        schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertIn("enable_image_compression", schema)
        self.assertIn("enable_compress_retry", schema)
        self.assertIn("recall_after_seconds", schema)

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
            "settings",
            "trigger_store",
            "image_files",
            "message_sender",
            "data.plugins.astrbot_plugin_img.main",
            "data.plugins.astrbot_plugin_img.settings",
            "data.plugins.astrbot_plugin_img.trigger_store",
            "data.plugins.astrbot_plugin_img.image_files",
            "data.plugins.astrbot_plugin_img.message_sender",
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
