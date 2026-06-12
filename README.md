# astrbot_plugin_nyaa_keyword_reply

> Nyaa简易关键字回复 —— 让群成员的关键词，按机器人**原生唤醒**逻辑触发回复。

监听群聊消息，当群成员发言命中你配置的关键词时，把这条消息**标记为"已唤醒机器人"**（等同于该消息 @ 了机器人），随后**完全交还给 AstrBot 原生流程**处理。回复内容、人格、上下文记忆、函数工具、@/引用格式等，**全部由机器人自身的配置决定**，本插件不参与。

## 兼容性

- 需要 AstrBot `>=4.0.0`。

## 设计理念

本插件**不是**一个独立的问答机器人，它只是一个**触发器**：

- ❌ 不持有自己的 LLM、API Key、system prompt 或人格设定；
- ❌ 不自己组装回复文本，也不自己决定 @ / 引用格式；
- ✅ 只在 AstrBot 原有的「唤醒」逻辑上，额外允许任意群成员用关键词触发与「被 @」完全相同的回复路径；
- ➕ 额外支持 QQ 号黑名单：命中黑名单的发送者，其一切群聊唤醒（关键词 / @ / 唤醒词）均被彻底忽略。

换句话说：**群友说出关键词 == 群友 @ 了机器人**，之后的一切都走 AstrBot 原生流程。

## 安装

本插件已发布至 AstrBot 插件市场，推荐通过 WebUI 一键安装：

1. 进入 AstrBot 管理面板（WebUI）。
2. 在左侧菜单栏点击 **插件**。
3. 进入 **插件市场**，搜索 `astrbot_plugin_nyaa_keyword_reply` 或 `Nyaa简易关键字回复`。
4. 点击 **安装** 并等待完成。
5. 安装后在已安装插件列表中找到本插件，进行配置即可。

*(手动安装方式：将本仓库克隆至 AstrBot 的 `data/plugins/` 目录中，然后重启 AstrBot。)*

## 配置项

| 配置 | 说明 |
| --- | --- |
| `trigger_keywords` | 普通包含匹配的关键词列表 |
| `regex_keywords` | 正则表达式关键词列表（高级） |
| `case_sensitive` | 关键词是否区分大小写（默认否） |
| `blacklist_user_ids` | 黑名单 QQ 号列表，命中者一切群聊唤醒（关键词 / @ / 唤醒词）均被忽略 |
| `enabled_groups` | 生效群白名单，留空对所有群生效 |
| `trigger_chance_percent` | 触发概率（百分比），降低打扰 |
| `cooldown_seconds` | 同群两次触发的最小间隔秒数 |
| `max_message_length` | 参与匹配的最大消息长度，0 表示不限 |

> 注意：机器人自身的群聊白名单、会话开关、频率限制、内容安全等**仍照常生效**——本插件只负责"触发"，不绕过任何原生限制。

## 黑名单

在配置项 `blacklist_user_ids` 中逐条填入要忽略的 **QQ 号**（与关键词相同的列表式添加交互）。命中黑名单的发送者：

- 说出触发关键词 —— 不触发；
- 直接 @ 机器人或使用唤醒词 —— 同样被忽略，机器人不回应。

即对这些 QQ 号，机器人**彻底无视其群聊唤醒**。黑名单判断优先级最高，先于关键词与群白名单。

> 黑名单基于 QQ 号（`event.get_sender_id()`，对应 OneBot 的 `user_id`），不依赖群名片 / 昵称——昵称可改可重复，QQ 号唯一稳定。

## 回复行为说明

回复是否 @ 发言成员、是否引用原消息、用什么人格和模型，**取决于 AstrBot 的平台配置与人格设置**（与机器人被 @ 时一致），不由本插件控制。想调整回复风格，请到 AstrBot 控制台对应配置处修改。

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

黑名单则相反：命中黑名单且消息已唤醒时，handler 置 `is_wake = False`、`is_at_or_wake_command = False`、`call_llm = True` 并调用 `event.stop_event()`，使上面的默认回复条件不成立，且后续管道阶段被 `scheduler` 在 `is_stopped()` 处中断，从而彻底忽略该用户。

## License

MIT
