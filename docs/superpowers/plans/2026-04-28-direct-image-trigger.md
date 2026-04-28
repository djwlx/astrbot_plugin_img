# Direct Image Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current mixed URL/API/local image plugin with a minimal direct image URL trigger plugin.

**Architecture:** Keep one plugin class in `main.py` with a simple `trigger_map` dictionary persisted to `config.json`. Add small pure helpers for trigger normalization, URL validation, and config migration so the behavior can be tested without running AstrBot. Runtime command handlers remain AstrBot event generators.

**Tech Stack:** Python, AstrBot plugin APIs, `aiohttp`, built-in `unittest`.

---

## File Structure

- `main.py`: minimal AstrBot plugin, direct URL trigger storage, add/delete commands, auto-trigger image sending.
- `tests/test_direct_image_plugin.py`: unit tests for trigger normalization, URL validation, config loading, and config saving. The test file stubs AstrBot modules before importing `main.py`.
- `README.md`: usage documentation for the two commands and direct trigger behavior.
- `metadata.yaml`: short description aligned with direct URL-only behavior.

### Task 1: Add Behavior Tests

**Files:**
- Create: `tests/test_direct_image_plugin.py`
- Test: `tests/test_direct_image_plugin.py`

- [ ] **Step 1: Write the failing tests**

```python
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
                            "old": {"type": "url", "value": "https://example.com/old.jpg"},
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

            self.main.save_trigger_map({"cat": "https://example.com/cat.jpg"}, config_path)

            result = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"trigger_map": {"cat": "https://example.com/cat.jpg"}})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests/test_direct_image_plugin.py -v`

Expected: FAIL because `normalize_trigger`, `is_http_url`, `load_trigger_map`, and `save_trigger_map` do not exist yet.

### Task 2: Rewrite Plugin

**Files:**
- Modify: `main.py`
- Test: `tests/test_direct_image_plugin.py`

- [ ] **Step 1: Implement the minimal direct URL plugin**

```python
import json
import logging
from pathlib import Path
from typing import Optional

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


def load_trigger_map(config_path: Path = DATA_FILE) -> dict[str, str]:
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
        normalize_trigger(trigger): url
        for trigger, url in raw_map.items()
        if isinstance(trigger, str) and isinstance(url, str) and is_http_url(url)
    }


def save_trigger_map(trigger_map: dict[str, str], config_path: Path = DATA_FILE) -> None:
    data = {"trigger_map": trigger_map}
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


@register("astrbot_plugin_img", "djwlx", "自定义图片触发词插件，仅支持直接图片URL", "v1.1.0")
class DirectImageTriggerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.trigger_map: dict[str, str] = load_trigger_map()

    @filter.command("添加图片触发词")
    async def add_image_trigger(self, event: AstrMessageEvent, trigger: str, url: str):
        trigger_key = normalize_trigger(trigger)
        if not trigger_key:
            yield event.plain_result("触发词不能为空")
            return
        if not is_http_url(url):
            yield event.plain_result("图片 URL 必须以 http:// 或 https:// 开头")
            return

        self.trigger_map[trigger_key] = url
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
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m unittest tests/test_direct_image_plugin.py -v`

Expected: PASS with 4 tests.

- [ ] **Step 3: Compile the plugin**

Run: `python -m py_compile main.py`

Expected: exit 0.

### Task 3: Update Documentation

**Files:**
- Modify: `README.md`
- Modify: `metadata.yaml`

- [ ] **Step 1: Replace README with direct URL usage**

```markdown
# astrbot_plugin_img

自定义图片触发词插件 for AstrBot。

> 极简图片发送插件：为触发词绑定一个直接图片 URL，发送触发词即可返回图片。

## 功能

- 添加图片触发词
- 删除图片触发词
- 发送触发词自动返回对应图片

## 安装

将插件文件放置在 AstrBot 插件目录下，并安装依赖：

```bash
pip install -r requirements.txt
```

## 使用说明

### 添加图片触发词

```text
/添加图片触发词 猫 https://example.com/cat.jpg
```

### 删除图片触发词

```text
/删除图片触发词 猫
```

### 发送图片

发送已配置的触发词即可自动回复图片：

```text
猫
```

## 注意事项

- 只支持直接图片 URL。
- 图片 URL 必须以 `http://` 或 `https://` 开头。
- 发送时插件会校验目标响应的 `Content-Type` 是否为 `image/*`。
- 不支持 API 返回 JSON、文本链接、本地图片路径或视频。

## 开发

基于 AstrBot 插件框架开发，使用 `aiohttp` 校验直接图片 URL。
```

- [ ] **Step 2: Update metadata description**

Set `metadata.yaml` description fields to direct URL-only wording:

```yaml
desc: 极简图片触发词插件，支持为触发词绑定直接图片URL。
version: v1.1.0
```

- [ ] **Step 3: Run verification**

Run: `python -m unittest tests/test_direct_image_plugin.py -v && python -m py_compile main.py`

Expected: tests pass and compile exits 0.
