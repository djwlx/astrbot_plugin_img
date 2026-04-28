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

## 开发

基于 AstrBot 插件框架开发，使用 `aiohttp` 下载直接图片 URL，并使用 `Pillow` 处理图片尺寸。
