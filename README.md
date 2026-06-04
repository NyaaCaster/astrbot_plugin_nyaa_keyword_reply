# astrbot_plugin_nyaa_keyword_reply

> Nyaa简易关键字回复 —— 让群成员的关键词，按机器人**原生唤醒**逻辑触发回复。

监听群聊消息，当群成员发言命中你配置的关键词时，把这条消息**标记为"已唤醒机器人"**（等同于该消息 @ 了机器人），随后**完全交还给 AstrBot 原生流程**处理。回复内容、人格、上下文记忆、函数工具、@/引用格式等，**全部由机器人自身的配置决定**，本插件不参与。

## 设计理念

本插件**不是**一个独立的问答机器人，它只是一个**触发器**：

- ❌ 不持有自己的 LLM、API Key、system prompt 或人格设定；
- ❌ 不自己组装回复文本，也不自己决定 @ / 引用格式；
- ✅ 只在 AstrBot 原有的「唤醒」逻辑上，额外允许任意群成员用关键词触发与「被 @」完全相同的回复路径。

换句话说：**群友说出关键词 == 群友 @ 了机器人**，之后的一切都走 AstrBot 原生流程。

## 工作原理

AstrBot 的消息管道顺序为：
`WakingCheckStage → WhitelistCheck → SessionStatusCheck → RateLimit → ContentSafety → PreProcess → ProcessStage → ...`

在 `ProcessStage` 中，插件 handler 先执行；handler 跑完后，框架判断：

```python
if (not event._has_send_oper
    and event.is_at_or_wake_command   # 被 @ 或唤醒前缀
    and not event.call_llm):
    # 用 event.message_str（群友原话）走机器人原生 LLM 回复
```

本插件的 handler 命中关键词后，仅置 `event.is_at_or_wake_command = True`（并 `is_wake = True`），且**自身不发送消息、不中断事件**，于是上面的条件成立，原生回复接管。

## 安装

AstrBot 插件目录为 `data/plugins/`。Docker 部署中 `docker-compose.yml` 已将 `./data` 映射进容器，因此把本插件目录放到宿主机 `data/plugins/` 下即可被加载：

```
data/plugins/astrbot_plugin_nyaa_keyword_reply/
├── __init__.py
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── logo.png
└── requirements.txt
```

放好后**重启 AstrBot 容器**（或在 WebUI 重载插件），并在插件配置页填写关键词。

## 配置项

| 配置 | 说明 |
| --- | --- |
| `trigger_keywords` | 普通包含匹配的关键词列表 |
| `regex_keywords` | 正则表达式关键词列表（高级） |
| `case_sensitive` | 关键词是否区分大小写（默认否） |
| `enabled_groups` | 生效群白名单，留空对所有群生效 |
| `trigger_chance_percent` | 触发概率（百分比），降低打扰 |
| `cooldown_seconds` | 同群两次触发的最小间隔秒数 |
| `max_message_length` | 参与匹配的最大消息长度，0 表示不限 |

> 注意：机器人自身的群聊白名单、会话开关、频率限制、内容安全等**仍照常生效**——本插件只负责"触发"，不绕过任何原生限制。

## 回复行为说明

回复是否 @ 发言成员、是否引用原消息、用什么人格和模型，**取决于 AstrBot 的平台配置与人格设置**（与机器人被 @ 时一致），不由本插件控制。想调整回复风格，请到 AstrBot 控制台对应配置处修改。

## 兼容性

- 需要 AstrBot `>=4.0.0`。

## License

AGPL-3.0
