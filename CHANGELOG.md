# Changelog

## v1.3.0 - 2026-04-28

- 拆分 `main.py`，将触发词存储、图片下载处理、发送/撤回逻辑拆到独立模块。
- 参考 `astrbot_plugin_recall_xz` 的 OneBot/aiocqhttp 思路：开启撤回时优先通过平台 API 发送图片并获取 `message_id`，再延时调用 `delete_msg`。
- 增加 Telegram 直接发送路径，开启撤回时可从 Telegram 返回对象中提取消息 ID。
- 增加版本维护规则：后续更新需同步修改 `metadata.yaml` 版本号和 `CHANGELOG.md`。

## v1.2.0 - 2026-04-28

- 新增 AstrBot 插件配置：图片压缩开关、压缩后重试开关、撤回秒数。
- 将触发词运行时数据保存到 `data/plugin_data/astrbot_plugin_img/triggers.json`，并兼容迁移旧数据。
- 增加本地图片发送路径，提升 Telegram、KOOK 等平台兼容性。
