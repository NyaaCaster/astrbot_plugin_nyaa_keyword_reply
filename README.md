# astrbot_plugin_nyaa_keyword_reply

> Nyaa简易关键字回复 —— 让 AstrBot 在群里像真人群友一样，根据群成员发言里的关键词主动接话。

监听群聊消息，当群成员的发言命中你配置的关键词时，Bot 会**引用**并 **@** 这位成员，调用当前会话的 LLM 生成一句自然的回复（参考 Bot 被 @ 时的对话方式）。也支持纯固定文本回复。

## 功能特性

- 🔑 **关键词触发**：普通包含匹配 + 正则匹配，二者可同时配置。
- 💬 **引用 + @ + LLM 回复**：命中后以「引用原消息 + @对方 + LLM 生成内容」的形式回复。
- 🧠 **复用现有 LLM**：直接调用 AstrBot 当前会话的提供商与人格设定，无需另配 API。
- 📌 **固定回复**：可为特定关键词配置写死的回复，命中时优先于 LLM。
- 🛡️ **防打扰**：同群冷却、触发概率、群白名单、最大消息长度等开关。
- 🚦 **不重复回复**：消息若本身已 @ Bot 或命中唤醒词，则交给主管道，本插件不介入。

## 安装

### 方式一：放入插件目录（推荐，Docker 同样适用）

AstrBot 的插件目录为 `data/plugins/`。在 Docker 部署中，`docker-compose.yml` 已将宿主机 `./data` 映射到容器内 `/AstrBot/data`，因此把本插件目录放到宿主机的 `data/plugins/` 下即可被容器加载：

```
data/plugins/astrbot_plugin_nyaa_keyword_reply/
├── __init__.py
├── main.py
├── metadata.yaml
├── _conf_schema.json
└── requirements.txt
```

放好后在 WebUI **插件管理**中刷新/重载插件，并在其**配置**页填写关键词。

### 方式二：WebUI 插件市场 / 仓库地址安装

在 WebUI 插件管理页填入本仓库地址安装：

```
https://github.com/NyaaCaster/astrbot_plugin_nyaa_keyword_reply
```

## 配置项

| 配置 | 说明 |
| --- | --- |
| `trigger_keywords` | 普通包含匹配的关键词列表 |
| `regex_keywords` | 正则表达式关键词列表（高级） |
| `case_sensitive` | 关键词是否区分大小写（默认否） |
| `use_llm` | 是否调用 LLM 生成回复（默认是） |
| `system_prompt` | LLM 人格设定 |
| `llm_prompt_template` | 发给 LLM 的提示词模板，支持 `{sender}`/`{message}`/`{keyword}` |
| `fixed_replies` | 固定回复，格式 `关键词=回复内容` |
| `at_sender` | 回复时是否 @ 发言成员（默认是） |
| `quote_message` | 回复时是否引用原消息（默认是） |
| `enabled_groups` | 生效群白名单，留空对所有群生效 |
| `trigger_chance_percent` | 触发概率（百分比），用于降低打扰 |
| `cooldown_seconds` | 同群两次主动搭话的最小间隔秒数 |
| `max_message_length` | 参与匹配的最大消息长度，0 表示不限 |
| `stop_after_reply` | 回复后是否中断事件传播，避免重复回复（默认是） |

## 工作原理

1. 通过 `@filter.event_message_type(EventMessageType.GROUP_MESSAGE)` 监听群消息。
2. 跳过已唤醒（被 @ / 唤醒词）的消息，避免与主对话流程重复。
3. 命中关键词后，用 `provider.text_chat()` 生成回复文本。
4. 用消息链 `[Reply, At, Plain]` 通过 `event.chain_result(...)` 发送，实现引用 + @ + 内容回复。

## 兼容性

- 需要 AstrBot `>=4.0.0`。
- 引用回复依赖平台对 `Reply` 组件的支持（QQ / aiocqhttp / NapCat 支持良好）。

## License

AGPL-3.0
