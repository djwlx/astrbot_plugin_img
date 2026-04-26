# astrbot_plugin_img

自定义图片插件 for AstrBot

> 简单易用的图片发送插件，支持 URL、API 和本地文件三种图片源。

## 功能

- **URL 模式**: 为触发词配置直接图片 URL，发送触发词自动返回对应图片
- **API 模式**: 配置动态图片 API，支持查询参数
- **本地模式**: 从本地文件夹中随机发送图片

## 安装

将插件文件放置在您的 AstrBot 插件目录下，并安装依赖：

```bash
pip install -r requirements.txt
```

## 使用说明

### URL 模式

```
/添加图片触发 猫 https://example.com/cat.jpg
/添加图片触发 狗 https://example.com/dog.png
```

发送 `猫` 或 `狗` 即可自动回复对应图片。

### API 模式

```
/设置图片API http://localhost:3000/pic
/添加API触发 随机 type=random
/测试图片API type=test
```

发送 `随机` 即可从 API 获取图片。

### 本地模式

```
/设置图片路径 /path/to/images
/添加本地图片 壁纸 wallpaper/
/添加本地图片 头像 avatar.jpg
```

发送 `壁纸` 会从 wallpaper 文件夹中随机选一张图片发送。

### 通用命令

| 命令 | 说明 |
|------|------|
| `/删除图片触发 [词]` | 删除触发词 |
| `/图片触发列表` | 查看所有触发词 |
| `/发送图片 [词]` | 手动发送图片 |
| `/开启图片插件` | 开启插件 |
| `/关闭图片插件` | 关闭插件 |
| `/图片帮助` | 查看帮助 |

## 支持的图片格式

jpg, jpeg, png, gif, bmp, webp

## 开发

基于 AstrBot 插件框架开发，支持异步 HTTP 请求和临时文件管理。
