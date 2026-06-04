"""Nyaa 简易关键字回复插件。

监听群聊消息，当群成员发言命中配置的关键词时，引用并 @ 该成员，
调用当前会话的 LLM 提供商生成一句自然的回复（参考 Bot 被 @ 时的对话方式）。

设计要点：
- 仅处理“未唤醒”的普通群消息：若该消息本身已经 @ 了 Bot 或命中唤醒词，
  则交给 AstrBot 主管道正常处理，本插件不介入，避免重复回复。
- 命中后通过 provider.text_chat 生成文本，再用消息链 [Reply, At, Plain] 回复，
  从而实现“引用 + @ + 内容回复”。
- 提供同群冷却、触发概率、群白名单、最大消息长度等防打扰开关。
"""

import random
import re
import time
from typing import Dict, List, Optional, Tuple

try:
    from astrbot.api.star import Context, Star, register
    from astrbot.api.event import filter, AstrMessageEvent
    from astrbot.api import logger
    import astrbot.api.message_components as Comp
except ImportError:  # 兼容个别旧版本的导入路径
    from astrbot.api.star import Context, Star, register
    from astrbot.api.event import filter, AstrMessageEvent
    from astrbot.api.utils import logger
    import astrbot.api.message_components as Comp

try:
    from astrbot.api.event import EventMessageType
except ImportError:
    from astrbot.api.event.filter import EventMessageType

__author__ = "NyaaCaster"
__signature__ = "Nyaa be with you."


@register(
    "astrbot_plugin_nyaa_keyword_reply",
    "NyaaCaster",
    "群聊关键词主动搭话：命中关键词后引用并@发言成员，由 LLM 生成回复。",
    "1.0.0",
    "https://github.com/NyaaCaster/astrbot_plugin_nyaa_keyword_reply",
)
class NyaaKeywordReplyPlugin(Star):
    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)
        # AstrBot 在存在 _conf_schema.json 时会把配置对象注入到 config 参数。
        self.config = config or {}
        # 记录每个群上次主动搭话的时间戳，用于冷却控制。
        self._last_reply_at: Dict[str, float] = {}
        self._reload_config()

    # ------------------------------------------------------------------ #
    # 配置解析
    # ------------------------------------------------------------------ #
    def _reload_config(self) -> None:
        cfg = self.config

        self.trigger_keywords: List[str] = [
            str(k).strip() for k in cfg.get("trigger_keywords", []) if str(k).strip()
        ]
        self.case_sensitive: bool = bool(cfg.get("case_sensitive", False))

        # 预编译正则，单条无效正则不影响其余规则。
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

        self.use_llm: bool = bool(cfg.get("use_llm", True))
        self.system_prompt: str = str(cfg.get("system_prompt", "") or "")
        self.llm_prompt_template: str = str(
            cfg.get("llm_prompt_template", "")
            or "群友「{sender}」刚刚说：「{message}」。请自然地接一句话。"
        )

        # 固定回复映射：支持 "关键词=回复" 形式。
        self.fixed_map: Dict[str, str] = {}
        for item in cfg.get("fixed_replies", []):
            item = str(item)
            if "=" not in item:
                continue
            key, _, value = item.partition("=")
            key, value = key.strip(), value.strip()
            if key and value:
                self.fixed_map[key if self.case_sensitive else key.lower()] = value

        self.at_sender: bool = bool(cfg.get("at_sender", True))
        self.quote_message: bool = bool(cfg.get("quote_message", True))

        self.enabled_groups = self._parse_id_set(cfg.get("enabled_groups", ""))

        self.trigger_chance: float = max(
            0.0, min(100.0, float(cfg.get("trigger_chance_percent", 100)))
        ) / 100.0
        self.cooldown_seconds: float = max(0.0, float(cfg.get("cooldown_seconds", 15)))
        self.max_message_length: int = int(cfg.get("max_message_length", 200))
        self.stop_after_reply: bool = bool(cfg.get("stop_after_reply", True))

    @staticmethod
    def _parse_id_set(raw) -> set:
        """把 '123,456\n789' 这类文本解析为字符串集合。"""
        if not raw:
            return set()
        parts = re.split(r"[\s,，、]+", str(raw))
        return {p.strip() for p in parts if p.strip()}

    # ------------------------------------------------------------------ #
    # 匹配逻辑
    # ------------------------------------------------------------------ #
    def _match_keyword(self, text: str) -> Optional[str]:
        """返回命中的关键词（普通词原文 / 正则模式串），未命中返回 None。"""
        haystack = text if self.case_sensitive else text.lower()
        for kw in self.trigger_keywords:
            needle = kw if self.case_sensitive else kw.lower()
            if needle in haystack:
                return kw
        for pattern in self.regex_patterns:
            if pattern.search(text):
                return pattern.pattern
        return None

    def _lookup_fixed_reply(self, text: str) -> Optional[str]:
        haystack = text if self.case_sensitive else text.lower()
        for key, value in self.fixed_map.items():
            if key in haystack:
                return value
        return None

    # ------------------------------------------------------------------ #
    # 事件监听
    # ------------------------------------------------------------------ #
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        # 1) 若消息本身已唤醒 Bot（被 @ 或命中唤醒词/指令），交给主管道，避免重复回复。
        if getattr(event, "is_at_or_wake_command", False):
            return
        if getattr(event, "is_wake", False):
            return

        # 2) 群白名单
        group_id = str(event.get_group_id() or "")
        if self.enabled_groups and group_id not in self.enabled_groups:
            return

        # 3) 文本预处理
        text = (event.get_plain_text() or "").strip()
        if not text:
            return
        if self.max_message_length and len(text) > self.max_message_length:
            return

        # 4) 不回复自己（保险，正常情况下框架不会把自身消息回灌）
        sender_id = str(event.get_sender_id() or "")
        if sender_id and sender_id == str(getattr(event, "get_self_id", lambda: "")() or ""):
            return

        # 5) 关键词匹配
        matched = self._match_keyword(text)
        if not matched:
            return

        # 6) 冷却 + 概率
        now = time.time()
        if now - self._last_reply_at.get(group_id, 0.0) < self.cooldown_seconds:
            return
        if self.trigger_chance < 1.0 and random.random() > self.trigger_chance:
            return

        # 7) 生成回复内容
        reply_text = await self._build_reply_text(event, text, matched)
        if not reply_text:
            return

        # 命中且确实要回复，才记录冷却时间，避免“被概率/LLM 失败跳过”也占用冷却。
        self._last_reply_at[group_id] = now

        # 8) 组装消息链：引用 + @ + 文本
        chain = self._compose_chain(event, reply_text)
        yield event.chain_result(chain)

        if self.stop_after_reply:
            event.stop_event()

    # ------------------------------------------------------------------ #
    # 回复构造
    # ------------------------------------------------------------------ #
    async def _build_reply_text(
        self, event: AstrMessageEvent, text: str, matched: str
    ) -> Optional[str]:
        # 固定回复优先（命中即用，不消耗 LLM）。
        fixed = self._lookup_fixed_reply(text)
        if fixed:
            return fixed

        if not self.use_llm:
            return None

        provider = self.context.get_using_provider(umo=event.unified_msg_origin)
        if provider is None:
            logger.warning("[nyaa_keyword_reply] 未配置可用的 LLM 提供商，跳过本次回复。")
            return None

        sender_name = ""
        try:
            sender_name = event.get_sender_name() or ""
        except Exception:
            pass
        sender_name = sender_name or "群友"

        prompt = self.llm_prompt_template.format(
            sender=sender_name, message=text, keyword=matched
        )

        try:
            resp = await provider.text_chat(
                prompt=prompt,
                context=[],
                system_prompt=self.system_prompt,
            )
        except Exception as exc:
            logger.error(f"[nyaa_keyword_reply] 调用 LLM 失败: {exc}")
            return None

        result = (getattr(resp, "completion_text", "") or "").strip()
        return result or None

    def _compose_chain(self, event: AstrMessageEvent, reply_text: str) -> list:
        chain: list = []

        # 引用对方原消息（依赖平台对 Reply 组件的支持，如 aiocqhttp/QQ）。
        if self.quote_message:
            message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
            if message_id is not None and hasattr(Comp, "Reply"):
                try:
                    chain.append(Comp.Reply(id=message_id))
                except Exception as exc:
                    logger.debug(f"[nyaa_keyword_reply] 构造引用失败，已跳过: {exc}")

        # @ 发言成员
        if self.at_sender:
            sender_id = event.get_sender_id()
            if sender_id:
                chain.append(Comp.At(qq=str(sender_id)))
                chain.append(Comp.Plain(" "))

        chain.append(Comp.Plain(reply_text))
        return chain
