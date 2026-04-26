import json
import os
import asyncio
import uuid
import random
import logging
from pathlib import Path
from typing import Optional

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Image

DATA_FILE = Path(__file__).parent / "config.json"
TEMP_DIR = Path(__file__).parent / "temp"
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

logger = logging.getLogger(__name__)


@register("astrbot_plugin_img", "djwlx",
          "自定义图片插件，支持URL/API/本地三种图片源",
          "v1.0.0")
class CustomImagePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        TEMP_DIR.mkdir(exist_ok=True)
        self.is_enabled: bool = True
        self.api_url: str = ""
        self.image_path: str = ""
        self.trigger_map: dict = {}
        self.load_config()

    def load_config(self):
        if DATA_FILE.exists():
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.is_enabled = data.get('is_enabled', True)
                self.api_url = data.get('api_url', '')
                self.image_path = data.get('image_path', '')
                self.trigger_map = data.get('trigger_map', {})
            except Exception:
                pass

    def save_config(self):
        data = {
            'is_enabled': self.is_enabled,
            'api_url': self.api_url,
            'image_path': self.image_path,
            'trigger_map': self.trigger_map,
        }
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    # ── URL 模式 ──

    @filter.command("添加图片触发")
    async def add_url_trigger(self, event: AstrMessageEvent, trigger: str, url: str):
        if not url.startswith(('http://', 'https://')):
            yield event.plain_result("URL 必须以 http:// 或 https:// 开头")
            return
        self.trigger_map[trigger.lower()] = {"type": "url", "value": url}
        self.save_config()
        yield event.plain_result(f"已添加 URL 触发: {trigger}")

    # ── API 模式 ──

    @filter.command("设置图片API")
    async def set_api_url(self, event: AstrMessageEvent, url: str):
        if not url.startswith(('http://', 'https://')):
            yield event.plain_result("API 地址必须以 http:// 或 https:// 开头")
            return
        self.api_url = url.rstrip('/')
        self.save_config()
        yield event.plain_result(f"图片 API 地址已设置: {self.api_url}")

    @filter.command("添加API触发")
    async def add_api_trigger(self, event: AstrMessageEvent, trigger: str, params: str = ""):
        if not self.api_url:
            yield event.plain_result("请先使用 /设置图片API 设置 API 地址")
            return
        self.trigger_map[trigger.lower()] = {"type": "api", "params": params}
        self.save_config()
        msg = f"已添加 API 触发: {trigger}"
        if params:
            msg += f"\n参数: {params}"
        yield event.plain_result(msg)

    @filter.command("测试图片API")
    async def test_api(self, event: AstrMessageEvent, params: str = ""):
        if not self.api_url:
            yield event.plain_result("请先使用 /设置图片API 设置 API 地址")
            return
        temp_path = await self._fetch_api_image(params)
        if temp_path:
            yield event.plain_result("API 测试成功，正在发送图片...")
            yield event.chain_result([Image(file=f"file://{temp_path}")])
            self._schedule_cleanup(temp_path)
        else:
            yield event.plain_result("API 测试失败，请检查地址和参数")

    # ── 本地模式 ──

    @filter.command("设置图片路径")
    async def set_image_path(self, event: AstrMessageEvent, path: str):
        if not os.path.exists(path):
            yield event.plain_result(f"路径不存在: {path}")
            return
        if not os.path.isdir(path):
            yield event.plain_result(f"不是文件夹: {path}")
            return
        self.image_path = path
        self.save_config()
        images = self._scan_images(path)
        yield event.plain_result(f"图片路径已设置: {path}\n发现 {len(images)} 张图片")

    @filter.command("添加本地图片")
    async def add_local_trigger(self, event: AstrMessageEvent, trigger: str, spec: str):
        if not self.image_path:
            yield event.plain_result("请先使用 /设置图片路径 设置图片文件夹")
            return
        full = os.path.join(self.image_path, spec)
        if os.path.isdir(full):
            files = self._scan_images(full)
            if not files:
                yield event.plain_result(f"文件夹内无图片: {spec}")
                return
        elif os.path.isfile(full):
            if Path(full).suffix.lower() not in IMAGE_EXTENSIONS:
                yield event.plain_result(f"不支持的图片格式: {spec}")
                return
            files = [spec]
        else:
            yield event.plain_result(f"文件或文件夹不存在: {spec}")
            return
        self.trigger_map[trigger.lower()] = {"type": "local", "files": files}
        self.save_config()
        yield event.plain_result(f"已添加本地图片触发: {trigger}\n包含 {len(files)} 张图片")

    # ── 通用命令 ──

    @filter.command("删除图片触发")
    async def delete_trigger(self, event: AstrMessageEvent, trigger: str):
        trigger = trigger.lower()
        if trigger in self.trigger_map:
            del self.trigger_map[trigger]
            self.save_config()
            yield event.plain_result(f"已删除触发: {trigger}")
        else:
            yield event.plain_result(f"触发不存在: {trigger}")

    @filter.command("图片触发列表")
    async def list_triggers(self, event: AstrMessageEvent):
        if not self.trigger_map:
            yield event.plain_result("暂无触发词")
            return
        type_names = {"url": "URL", "api": "API", "local": "本地"}
        msg = "触发词列表:\n"
        for k, v in self.trigger_map.items():
            t = type_names.get(v["type"], v["type"])
            if v["type"] == "url":
                detail = v["value"]
            elif v["type"] == "api":
                detail = v.get("params") or "无参数"
            else:
                detail = f"{len(v.get('files', []))} 张图片"
            msg += f"\n[{t}] {k}\n  {detail}"
        yield event.plain_result(msg)

    @filter.command("发送图片")
    async def send_image_cmd(self, event: AstrMessageEvent, trigger: Optional[str] = None):
        if not trigger:
            yield event.plain_result("请指定触发词: /发送图片 [触发词]")
            return
        trigger = trigger.lower()
        if trigger not in self.trigger_map:
            yield event.plain_result(f"触发词不存在: {trigger}")
            return
        async for result in self._send_by_trigger(event, trigger):
            yield result

    @filter.command("开启图片插件")
    async def enable_plugin(self, event: AstrMessageEvent):
        self.is_enabled = True
        self.save_config()
        yield event.plain_result("图片插件已开启")

    @filter.command("关闭图片插件")
    async def disable_plugin(self, event: AstrMessageEvent):
        self.is_enabled = False
        self.save_config()
        yield event.plain_result("图片插件已关闭")

    @filter.command("图片帮助")
    async def help_cmd(self, event: AstrMessageEvent):
        msg = """自定义图片插件帮助

通用命令:
  /开启图片插件     - 开启插件
  /关闭图片插件     - 关闭插件
  /删除图片触发 [词] - 删除触发词
  /图片触发列表      - 查看所有触发词
  /发送图片 [词]    - 手动发送图片
  /图片帮助         - 显示本帮助

URL 模式:
  /添加图片触发 [词] [URL]
  示例: /添加图片触发 猫 https://example.com/cat.jpg

API 模式:
  /设置图片API [URL]
  /添加API触发 [词] [参数]
  /测试图片API [参数]
  示例: /设置图片API http://localhost:3000/pic
        /添加API触发 随机 type=random

本地模式:
  /设置图片路径 [路径]
  /添加本地图片 [词] [文件/文件夹名]
  示例: /设置图片路径 /path/to/images
        /添加本地图片 狗 dogs/

直接发送触发词即可自动发送对应图片"""
        yield event.plain_result(msg)

    # ── 自动触发 ──

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.is_enabled:
            return
        msg = event.message_str.strip().lower()
        if msg.startswith('/'):
            return
        if msg in self.trigger_map:
            async for result in self._send_by_trigger(event, msg):
                yield result

    # ── 内部方法 ──

    async def _send_by_trigger(self, event: AstrMessageEvent, trigger: str):
        entry = self.trigger_map[trigger]
        typ = entry["type"]

        if typ == "url":
            resolved = await self._resolve_url(entry["value"])
            if resolved:
                yield event.chain_result([Image(file=resolved)])
                event.stop_event()
            else:
                yield event.plain_result("获取 URL 图片失败")

        elif typ == "api":
            if not self.api_url:
                yield event.plain_result("请先设置 API 地址")
                return
            temp_path = await self._fetch_api_image(entry.get("params", ""))
            if temp_path:
                yield event.chain_result([Image(file=f"file://{temp_path}")])
                event.stop_event()
                self._schedule_cleanup(temp_path)
            else:
                yield event.plain_result("获取 API 图片失败")

        elif typ == "local":
            if not self.image_path:
                yield event.plain_result("请先设置图片路径")
                return
            files = entry.get("files", [])
            if not files:
                yield event.plain_result("该触发词无图片")
                return
            chosen = random.choice(files)
            full = os.path.join(self.image_path, chosen)
            if os.path.exists(full):
                yield event.chain_result([Image(file=f"file://{full}")])
                event.stop_event()
            else:
                yield event.plain_result(f"图片文件不存在: {chosen}")

    async def _resolve_url(self, url: str) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        ct = resp.headers.get('Content-Type', '').lower()
                        if ct.startswith('image/'):
                            return str(resp.url)
        except Exception as e:
            logger.error(f"解析图片 URL 失败: {e}")
        return None

    async def _fetch_api_image(self, params: str) -> Optional[str]:
        if not self.api_url:
            return None
        try:
            fetch_url = f"{self.api_url}?{params}" if params else self.api_url
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    fetch_url,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"API 返回状态码: {resp.status}")
                        return None
                    ct = resp.headers.get('Content-Type', '').lower()
                    if not ct.startswith('image/'):
                        logger.error(f"API 返回非图片类型: {ct}")
                        return None
                    data = await resp.read()
                    ext = self._ext_from_content_type(ct)
                    fname = f"{uuid.uuid4()}{ext}"
                    fpath = TEMP_DIR / fname
                    with open(fpath, 'wb') as f:
                        f.write(data)
                    return str(fpath)
        except Exception as e:
            logger.error(f"获取 API 图片失败: {e}")
        return None

    @staticmethod
    def _ext_from_content_type(content_type: str) -> str:
        if 'png' in content_type:
            return '.png'
        if 'gif' in content_type:
            return '.gif'
        if 'bmp' in content_type:
            return '.bmp'
        if 'webp' in content_type:
            return '.webp'
        return '.jpg'

    def _schedule_cleanup(self, file_path: str):
        async def delayed():
            await asyncio.sleep(30)
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.error(f"清理临时文件失败: {e}")
        asyncio.create_task(delayed())

    @staticmethod
    def _scan_images(folder: str) -> list:
        images = []
        for root, dirs, files in os.walk(folder):
            for fname in files:
                if Path(fname).suffix.lower() in IMAGE_EXTENSIONS:
                    images.append(os.path.relpath(os.path.join(root, fname), folder))
        return images
