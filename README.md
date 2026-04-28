# astrbot_plugin_img

自定义图片触发词插件 for AstrBot。

> 极简图片发送插件：为触发词绑定一个直接图片 URL，发送触发词即可返回图片。

## 功能

- 添加图片触发词
- 删除图片触发词
- 列出所有图片触发词
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

### 列出图片触发词

```text
/图片触发词列表
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
- 发送时会先把图片下载到本地临时文件，再交给 AstrBot 发送，便于适配 Telegram、KOOK 等平台。
- 对非动图图片，插件会尝试按 Telegram `sendPhoto` 的尺寸限制进行缩放或补边处理。
- 不支持 API 返回 JSON、文本链接、本地图片路径或视频。

## 数据存储

- 触发词数据保存到 `data/plugin_data/astrbot_plugin_img/triggers.json`。
- 临时图片保存到 `data/plugin_data/astrbot_plugin_img/temp/`，发送后会延迟清理。
- 旧版本插件根目录下的 `config.json` 或 `data/plugin_data/astrbot_plugin_img/config.json` 会在首次启动时自动迁移到新位置。
- `data/config/` 是 AstrBot 核心配置和插件 `_conf_schema.json` 配置目录，不用于保存本插件运行时触发词数据。

## 插件配置

AstrBot 会根据 `_conf_schema.json` 在 `data/config/astrbot_plugin_img_config.json` 生成插件配置。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_image_compression` | `true` | 发送前是否压缩/补边图片。关闭后下载原图并直接发送。 |
| `enable_compress_retry` | `true` | 原图发送失败后，是否压缩/补边并重试一次。 |
| `recall_after_seconds` | `0` | 多少秒后撤回图片。`0` 表示不撤回；仅在平台适配器能返回已发送消息 ID 并支持删除消息时生效。 |

## 版本维护

- 每次功能更新、修复或重构都需要同步更新 `metadata.yaml` 的 `version` 和 `CHANGELOG.md`。
- 版本号按语义化规则选择：破坏兼容升主版本，新增能力升次版本，修复或文档小改升修订版本。

## 开发

基于 AstrBot 插件框架开发，使用 `aiohttp` 下载直接图片 URL，并使用 `Pillow` 处理图片尺寸。OneBot/aiocqhttp 平台在开启撤回时会直接调用平台发送 API 以获取 `message_id`。
