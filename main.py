"""Nyaa 简易关键字回复插件。

只做一件事：当群成员发言命中配置的关键词时，把该消息事件标记为「已唤醒」
（等同于该消息 @ 了机器人 / 命中了唤醒词），随后完全交还给 AstrBot 原生管道，
由机器人自身配置的提供商、人格、对话上下文、函数工具等生成并发送回复。

因此本插件：
- 不自己调用 LLM、不持有任何 system prompt / 人格设定；
- 不自己组装 @ / 引用 等回复格式——回复方式由 AstrBot 平台配置原生决定；
- 仅相当于在「唤醒词」原有逻辑上，额外允许任意群成员用关键词触发同样的唤醒回复。

原理（见 AstrBot 源码 astrbot/core/pipeline）：
ProcessStage 在执行完插件 handler 后，若满足
``not event._has_send_oper and event.is_at_or_wake_command and not event.call_llm``
便会调用默认 LLM agent，用 ``event.message_str``（群友原话）走原生回复流程。
本插件 handler 正是在该判断之前执行，只需置 ``event.is_at_or_wake_command = True``，
自身不发送、不中断事件，即可让原生回复接管。
"""

import random
import re
import time
from typing import Dict, List, Optional

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger

try:
    from astrbot.api.event import EventMessageType
except ImportError:
    from astrbot.api.event.filter import EventMessageType

__author__ = "NyaaCaster"
__signature__ = "Nyaa be with you."


@register(
    "astrbot_plugin_nyaa_keyword_reply",
    "NyaaCaster",
    "群聊关键词触发：命中关键词即按机器人原生唤醒逻辑回复（复用 bot 自身的 provider/人格/上下文）；支持 QQ 号黑名单彻底忽略其群聊唤醒。",
    "2.1.1",
    "https://github.com/NyaaCaster/astrbot_plugin_nyaa_keyword_reply",
)
class NyaaKeywordReplyPlugin(Star):
    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)
        self.config = config or {}
        # 每个群上次触发时间，用于冷却控制。
        self._last_trigger_at: Dict[str, float] = {}
        # 注意：不在 __init__ 里解析配置快照——AstrBot 的插件配置对象可能在
        # 运行中被原地更新（AstrBotConfig.save_config 的 self.update），且插件
        # 会因 WebUI 保存配置被热重载；为保证黑名单/关键词永远反映最新配置，
        # 改为在 on_group_message 每次执行时实时同步（见 _sync_runtime_config）。
        self._sync_runtime_config()

    # ------------------------------------------------------------------ #
    # 配置解析
    # ------------------------------------------------------------------ #
    def _sync_runtime_config(self) -> None:
        """实时同步运行所需配置。

        每次消息处理时都会调用，代价极小（解析一个小配置 dict），换来：
        1. WebUI 保存配置后（AstrBotConfig 被原地 update），无需等待插件
           重载即生效；
        2. 插件热重载/配置漂移后，黑名单与关键词永远反映最新配置，杜绝
           因初始化快照陈旧导致的黑名单失效。

        注意：AstrBot 在「保存插件配置 → reload 插件」的重载窗口内，本插件
        on_group_message handler 会被临时解绑（_unbind_plugin），此时任何
        插件逻辑都无法拦截消息——这是 AstrBot 核心 reload 机制的固有限制，
        插件层面只能通过实时同步把其余失效路径全部消除。
        """
        cfg = self.config

        self.trigger_keywords: List[str] = [
            str(k).strip() for k in cfg.get("trigger_keywords", []) if str(k).strip()
        ]
        self.case_sensitive: bool = bool(cfg.get("case_sensitive", False))

        self.regex_patterns: List[re.Pattern] = []
        flags = 0 if self.case_sensitive else re.IGNORECASE
        for raw in cfg.get("regex_keywords", []):
            raw = str(raw).strip()
            if not raw:
                continue
            try:
                self.regex_patterns.append(re.compile(raw, flags))
            except re.error as exc:
                logger.warning(f"[nyaa_keyword_reply] 忽略无效正则 {raw!r}: {exc}")

        self.enabled_groups = self._parse_id_set(cfg.get("enabled_groups", ""))
        self.trigger_chance: float = max(
            0.0, min(100.0, float(cfg.get("trigger_chance_percent", 100)))
        ) / 100.0
        self.cooldown_seconds: float = max(0.0, float(cfg.get("cooldown_seconds", 15)))
        self.max_message_length: int = int(cfg.get("max_message_length", 200))

        # 黑名单 QQ 号：命中则彻底无视该用户的群聊唤醒（关键词 / @ / 唤醒词）。
        # aiocqhttp(OneBot) 下 event.get_sender_id() 返回的就是发送者 QQ 号字符串。
        self.blacklist_user_ids: set = {
            str(u).strip()
            for u in cfg.get("blacklist_user_ids", [])
            if str(u).strip()
        }

    @staticmethod
    def _parse_id_set(raw) -> set:
        if not raw:
            return set()
        parts = re.split(r"[\s,，、]+", str(raw))
        return {p.strip() for p in parts if p.strip()}

    # ------------------------------------------------------------------ #
    # 关键词匹配
    # ------------------------------------------------------------------ #
    def _match_keyword(self, text: str) -> Optional[str]:
        haystack = text if self.case_sensitive else text.lower()
        for kw in self.trigger_keywords:
            needle = kw if self.case_sensitive else kw.lower()
            if needle in haystack:
                return kw
        for pattern in self.regex_patterns:
            if pattern.search(text):
                return pattern.pattern
        return None

    # ------------------------------------------------------------------ #
    # 事件监听：命中关键词 -> 标记唤醒 -> 交还原生管道
    # ------------------------------------------------------------------ #
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        # 每条消息实时同步配置（见 _sync_runtime_config 注释）：
        # 保证黑名单与关键词始终是最新值，即使配置对象被原地 update 或
        # 插件刚被热重载，也无需等待初始化快照。
        self._sync_runtime_config()

        sender_id = str(event.get_sender_id() or "")

        # 黑名单（最高优先级）：彻底无视该 QQ 号的一切群聊唤醒——关键词、@、唤醒词皆然。
        # 若该消息已被原生唤醒（如 @ 机器人），撤销唤醒标志并中止管道，使机器人不回应。
        if sender_id and sender_id in self.blacklist_user_ids:
            if getattr(event, "is_wake", False) or getattr(
                event, "is_at_or_wake_command", False
            ):
                event.is_wake = False
                event.is_at_or_wake_command = False
                event.call_llm = True  # 禁止默认 LLM 回复（与下方 stop_event 双保险）
                event.stop_event()  # 置 _has_stopped，scheduler 在 stage 间 break 后续处理
                logger.info(
                    f"[nyaa_keyword_reply] 黑名单用户 {sender_id} 的群聊唤醒已被拦截并忽略。"
                )
            return

        # 该消息本就已唤醒机器人（被 @ / 唤醒词 / 引用 bot），无需介入，避免重复。
        if getattr(event, "is_at_or_wake_command", False):
            return

        text = (event.message_str or "").strip()
        if not text:
            return
        if self.max_message_length and len(text) > self.max_message_length:
            return

        # 不处理机器人自己的消息（保险）。
        self_id = str(getattr(event, "get_self_id", lambda: "")() or "")
        if sender_id and self_id and sender_id == self_id:
            return

        # 群白名单（留空则所有群生效）。
        group_id = str(event.get_group_id() or "")
        if self.enabled_groups and group_id not in self.enabled_groups:
            return

        # 关键词匹配。
        matched = self._match_keyword(text)
        if not matched:
            return

        # 冷却 + 概率（纯触发频率控制，不涉及任何 LLM 逻辑）。
        now = time.time()
        if now - self._last_trigger_at.get(group_id, 0.0) < self.cooldown_seconds:
            return
        if self.trigger_chance < 1.0 and random.random() > self.trigger_chance:
            return
        self._last_trigger_at[group_id] = now

        # 唯一动作：把事件标记为「如同被唤醒」。
        # 之后 ProcessStage 会用本条消息（event.message_str）走机器人原生 LLM 回复，
        # 回复的人格、上下文、provider、@/引用 等全部由 AstrBot 自身配置决定。
        # 本 handler 不 yield 结果、不 event.send、不 stop_event，以免阻断原生回复。
        event.is_wake = True
        event.is_at_or_wake_command = True
        logger.info(
            f"[nyaa_keyword_reply] 群 {group_id} 命中关键词「{matched}」，转交机器人原生唤醒回复。"
        )
