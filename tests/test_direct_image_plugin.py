import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


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


if __name__ == "__main__":
    unittest.main()
