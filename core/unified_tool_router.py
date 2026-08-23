"""统一工具调度层 — 本地能力 / 本地插件工具 / Hanako 服务端工具 三类来源统一快速路由

背景：oc-pet 桌宠现有三层能力
1. 本地能力  core/capability_registry.py：CAPABILITIES 静态列表（确定性动作，如 暂停/日报/截图/随机播放）
2. 本地插件工具 core/tool_registry.py：Hanako 全局插件 + oc-pet 本地插件（如 linjian-peek 手机控制 8 工具）
3. Hanako 服务端工具：WS 模式（M4）下工具由服务端执行，桌宠不传 tools

本层把它们统一到一个快速路由，路由顺序（快→慢）：
1. 显式指定：文本含"用X"/"调用X"/"使用X"/"X工具" → 精确匹配插件工具名（意图最明确，优先）
2. 静态能力：先试 CapabilityRouter（内置确定性能力，如"暂停一下"→pause_music）
3. 关键词命中（白名单模式）：只对**确定性安全工具**做关键词匹配 → 直达插件工具。
   白名单 _KEYWORD_ROUTE_WHITELIST：hanako-audio-player 的 audio_bus 控制类 +
   linjian-peek 手机控制工具 + oc-pet 本地插件工具。
   其余 Hanako 插件工具（tavily 搜索 / webpage 存档 / 图库 / RSS / B站 / 待办 /
   时间统计 / 表情包等）**不参与关键词匹配**——由 Hanako 端 LLM 按工具名+描述+
   参数 schema 语义自动选工具（oc-pet 侧写 pattern 是"抢活"，R3 已收敛）。
4. 兜底：返回 None（交给 LLM 工具调用 / Hanako 服务端）

注意：播放类自然语言（"播首歌"/"播放周杰伦的晴天"/"搜一下XXX"）不再被静态能力/
关键词路由拦截——这类表达直接走第 4 步交给 LLM 自动选工具，像 Hana 原界面一样自然。
仅"随机放/来一首"（Bug B 本地直达）与 audio_bus 固定动作（暂停/下一首/清空等）
仍由本地快速路由兜住。

疑问句不路由：文本含"你能…吗/可以…吗/会…吗/能不能/可不可以/知道…吗"等疑问句式时，
关键词路由直接返回 None——能力询问交给 LLM 回答，不再误触本地工具（如 list_stickers）。

Hanako WS 模式（M4）协议不变：本地统一路由命中 → 直接返回 RouteResult 文案；
未命中 → 走 LLM / 服务端工具。

插件热刷新：ToolRegistry.refresh() 重新扫描插件目录（清空旧索引），
UnifiedToolRouter.refresh() 重建关键词索引；conversation_engine 每 30s 调一次，
新增/删除插件 30 秒内生效，无需重启桌宠。

用法:
    router = UnifiedToolRouter(tool_executor=executor)
    result = router.route("用phone-peek-screen看下手机", tool_registry=reg, static_router=cap_router)
    if result:
        # 直接执行，不走 LLM
        on_reply(result.text, result.emotion, result.anim)
    # None → 兜底 LLM / Hanako 服务端
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional, Tuple

from .capability_registry import RouteResult

logger = logging.getLogger(__name__)

# 显式指定解析正则：
#   (用|调用|使用|让|请|帮我)? 前缀动作词（可选）
#   ([\w-]+)                   候选 token（工具名，允许连字符/下划线）
#   (工具|插件|来做|去|执行)?   后缀（可选）
_EXPLICIT_RE = re.compile(
    r'(?:用|调用|使用|让|请|帮我)?\s*([a-zA-Z0-9_-]+)\s*(?:工具|插件|来做|去|执行)?'
)

# P7: 成对格式“用<插件名>的<工具名>功能”/“用<插件名>-<工具名>”
_EXPLICIT_PAIR_RE = re.compile(
    r'(?:用|调用|使用|让|请|帮我)?\s*([a-zA-Z0-9_.-]+)\s*(?:的|[-/])\s*([a-zA-Z0-9_-]+)\s*(?:功能|工具|插件)?'
)

# 英文工具名 token → 中文同义词（name 拆分兜底，让"screen"能命中"屏幕"）
_EN_TO_ZH = {
    "phone": "手机",
    "screen": "屏幕",
    "state": "状态",
    "status": "状态",
    "peek": "查看",
    "notif": "通知",
    "notification": "通知",
    "alarm": "闹钟",
    "home": "主页",
    "app": "应用",
    "open": "打开",
    "life": "生活",
    "image": "图片",
    "device": "设备",
    "control": "控制",
    "list": "列表",
    "search": "搜索",
    "weather": "天气",
    "read": "读取",
    "send": "发送",
}

# 描述里常见中文名词：从 ToolDef.description 提取关键词（权重低，防误伤）。
# R3 白名单化后只对确定性工具（audio_bus + linjian-peek 手机控制）建索引，
# 通用词收敛到这两类工具相关词即可；搜索/订阅/待办/网页等词已移除（那些工具的
# 描述不再参与关键词匹配，交给 Hanako LLM）。
_COMMON_ZH_WORDS = [
    "手机", "屏幕", "状态", "通知", "闹钟", "电量", "应用",
    "桌面", "主页", "打开", "启动", "播放", "音频", "音乐", "队列",
]

# 手工关键词映射：工具名 → 额外关键词（插件少，先手工维护 + name 拆分兜底）
_TOOL_KEYWORD_MAP: dict[str, list[str]] = {
    "phone_peek_screen": ["手机", "屏幕", "截图", "查看手机", "看手机", "看屏幕", "手机屏幕", "peek", "screen"],
    "phone_state": ["手机", "状态", "前台", "应用", "屏幕文字", "state"],
    "phone_life_state": ["手机", "状态", "电量", "生活", "life"],
    "phone_open_app": ["打开", "启动", "应用", "打开应用", "启动应用", "打开手机", "open", "app"],
    "phone_home": ["回到桌面", "返回桌面", "主页", "手机桌面", "home"],
    "phone_notification": ["通知", "消息", "通知栏", "notification"],
    "phone_alarm": ["闹钟", "设置闹钟", "alarm"],
    "phone_status": ["手机状态", "状态", "信息", "在线", "连接", "status"],
    # audio_bus：固定 action 控制（pause/resume/next/state/clear 等）——静态能力
    # 未覆盖的说法（如"切换下一曲"）由关键词路由兜底直达。
    "audio_bus": ["暂停", "继续播放", "下一首", "切歌", "清空播放列表", "播放状态", "音乐状态", "audio", "bus"],
}

# ── R3 关键词路由白名单 ──────────────────────────────────────
# 只对白名单内的**确定性安全工具**构建关键词索引；非白名单 Hanako 插件工具
# 不参与关键词匹配，直接让位 Hanako LLM 按语义选工具。
# 1) 工具名白名单：hanako-audio-player 的 audio_bus 控制类（固定 action 本地直达）。
# 2) 插件白名单：linjian-peek（oc-pet 本地插件，手机控制 8 工具）。
#    未来新增 oc-pet 本地插件工具时在此追加 plugin_id。
_KEYWORD_ROUTE_TOOL_WHITELIST: set[str] = {
    "audio_bus",
}
_KEYWORD_ROUTE_PLUGIN_WHITELIST: set[str] = {
    "linjian-peek",
}

# 疑问句触发结构（关键词路由直接返回 None，交给 LLM 回答能力询问）：
#   "你能…吗" / "可以…吗" / "会…吗" / "能不能…" / "可不可以…" / "知道…吗" / "会不会…"
_QUESTION_RE = re.compile(
    r"(你能|你可以|你会|你能不能|你可不可以|能不能|可不可以|会不会|知不知道|知道不知道|"
    r"知道|可以|会|能)"
    r".{0,8}?"
    r"(吗|么|嘛|呢|？|\?)"
)


class UnifiedToolRouter:
    """统一工具调度层：三类工具来源快速路由。

    路由顺序（快→慢）：
    1. 显式指定：文本含"用X"/"调用X"/"使用X"/"X工具" → 精确匹配插件工具名（优先）
    2. 静态能力：先试 static_router（CapabilityRouter，内置确定性能力）
    3. 关键词命中（白名单模式）：只对白名单内确定性安全工具（audio_bus +
       linjian-peek 手机控制 + oc-pet 本地插件）做关键词匹配 → 直达插件工具；
       疑问句（你能…吗/可以…吗/能不能…等）不路由；非白名单 Hanako 插件工具
       不参与关键词匹配，让位 Hanako LLM。
    4. 兜底：返回 None（交给 LLM 工具调用 / Hanako 服务端）
    """

    def __init__(self, perception=None, tool_executor=None):
        self._perception = perception          # 透传给静态路由/未来内部能力
        self._tool_executor = tool_executor    # 执行本地插件工具
        self._tool_registry = None             # 最近一次 refresh 的注册表
        self._keyword_index: dict[str, dict[str, int]] = {}  # 工具名 -> {关键词: 权重}
        self._last_refresh: float = time.monotonic()

    # ── 对外主入口 ──────────────────────────────────────────

    def route(self, text: str, tool_registry=None, static_router=None,
              execute: bool = True) -> Optional[RouteResult]:
        """统一路由：插件工具（显式/白名单关键词）→ 静态能力 → None（兜底 LLM）

        Args:
            text: 用户文本（文字或语音转写后的文本）
            tool_registry: ToolRegistry（本地插件工具）
            static_router: CapabilityRouter（内置确定性能力，显式插件之后）
            execute: 命中插件工具后是否立即执行（False → 返回"工具执行中"标记）

        Returns:
            RouteResult 命中；None 需回退到 LLM / Hanako 服务端
        """
        if not text or not text.strip():
            return None
        text = text.strip()

        # 1. 插件工具：显式指定优先（“用X/Y”意图最明确）。
        #    必须在静态能力之前——否则“用hanako-audio-player的play”可能被某个
        #    宽 pattern 的静态能力抢先截胡（历史教训：play_music 的 'play' 曾拦截
        #    显式调用导致“找不到工具 play”）。
        reg = tool_registry or self._tool_registry
        if reg is not None:
            hit = self._match_explicit(text, reg)
            if hit is not None:
                logger.info("统一路由 → 显式指定: %s (plugin=%s)", hit[0], hit[1])
                return self._build_tool_result(hit, reg, execute, "explicit")

        # 2. 静态能力（内置确定性能力：暂停/下一首/日报/截图/搜索等）
        if static_router is not None:
            try:
                result = static_router.route(text)
                if result is not None:
                    logger.info("统一路由 → 静态能力: %s", result.capability)
                    return result
            except Exception as e:
                logger.warning("统一路由：静态能力异常: %s", e)

        # 3. 插件工具：关键词命中（白名单模式——只匹配确定性安全工具，
        #    非白名单 Hanako 插件工具让位 Hanako LLM；疑问句不路由）
        if reg is not None:
            hit = self._match_keyword(text, reg)
            if hit is not None:
                logger.info("统一路由 → 关键词命中: %s (plugin=%s)", hit[0], hit[1])
                return self._build_tool_result(hit, reg, execute, "keyword")

        # 4. 兜底：交给 LLM 工具调用 / Hanako 服务端
        return None

    # ── 二级：显式指定 ──────────────────────────────────────

    def _match_explicit(self, text: str, tool_registry) -> Optional[Tuple[str, str]]:
        """解析显式指定：返回 (tool_name, plugin_id) 或 None

        优先尝试成对格式“用<插件名>的<工具名>功能”，再回退单 token。
        归一化后与工具名精确匹配（忽略大小写、去下划线/连字符）。
        """
        tools = getattr(tool_registry, "_tools", {}) or {}

        # P7: 成对格式——“用hanako-audio-player的play” → (plugin, tool)
        mp = _EXPLICIT_PAIR_RE.search(text)
        if mp and mp.group(1) and mp.group(2):
            plugin_tok = mp.group(1)
            tool_tok = mp.group(2)
            t_norm = self._normalize_name(tool_tok)
            p_norm = self._normalize_name(plugin_tok)
            for tool in tools.values():
                if t_norm and self._normalize_name(tool.name) == t_norm:
                    # 工具名命中；若给了插件名则校验归属（不匹配则跳过）
                    if p_norm and tool.plugin_id and self._normalize_name(tool.plugin_id) != p_norm:
                        continue
                    return tool.name, tool.plugin_id
            # 成对模式给了明确工具名但没找到 → 不降级（避免误匹配其他工具）
            return None

        token = self._parse_explicit_token(text)
        if not token:
            return None
        norm = self._normalize_name(token)
        if not norm:
            return None

        # 精确匹配工具名（归一化后：phone_peek_screen == phone-peek-screen）
        for tool in tools.values():
            if self._normalize_name(tool.name) == norm:
                return tool.name, tool.plugin_id
        # plugin_id 匹配（如 "linjian-peek"）
        for tool in tools.values():
            if tool.plugin_id and self._normalize_name(tool.plugin_id) == norm:
                return tool.name, tool.plugin_id
        # 通过 _name_map（sanitized 名称，如 phone_peek_screen）
        name_map = getattr(tool_registry, "_name_map", {}) or {}
        original = name_map.get(token)
        if original and original in tools:
            return tools[original].name, tools[original].plugin_id
        return None

    def _parse_explicit_token(self, text: str) -> Optional[str]:
        """从文本中提取候选工具名 token。"""
        m = _EXPLICIT_RE.search(text)
        if not m:
            return None
        token = m.group(1)
        # 正则前缀没吃掉的动作词（如 "请用phone" → token 以"用"开头）
        for w in ("调用", "使用", "帮我用", "请用", "用", "让", "请"):
            if token.startswith(w):
                token = token[len(w):]
                break
        # 优先取拉丁字母/数字/连字符前缀（工具名主体，如 phone-peek-screen）
        latin = re.match(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', token)
        if latin:
            return latin.group(0)
        # 纯中文短 token（≤6 字）保留（尝试匹配插件显示名/中文工具名）
        if re.fullmatch(r'[\u4e00-\u9fff]{1,6}', token):
            return token
        return None

    # ── 三级：关键词命中 ────────────────────────────────────

    def _match_keyword(self, text: str, tool_registry) -> Optional[Tuple[str, str]]:
        """关键词路由（白名单模式）：白名单内工具名/描述关键词匹配用户文本。

        - 疑问句（你能…吗/可以…吗/会不会…/能不能…等）直接返回 None——
          能力询问交给 LLM 回答，不再误触本地工具（如 list_stickers）。
        - 打分：工具名整体(5) > 工具名 token+同义词(3) = 手工映射(3) > 描述词(1)。
          取最高分工具；同分取关键词命中数更多者。
        - 索引只含白名单工具（_build_keyword_index 过滤），非白名单 Hanako 插件
          工具不参与匹配，让位 Hanako LLM。
        """
        text_lower = text.lower()
        if self._is_question_text(text_lower):
            logger.debug("关键词路由跳过疑问句: %s", text[:30])
            return None
        index = self._keyword_index
        if not index:
            return None

        best_name: Optional[str] = None
        best_score = 0
        best_kw_count = 0
        for name, kws in index.items():
            score = 0
            kw_count = 0
            for kw, weight in kws.items():
                if kw and self._kw_in_text(kw, text_lower):
                    score += weight
                    kw_count += 1
            if score > best_score or (score == best_score and kw_count > best_kw_count):
                best_score = score
                best_kw_count = kw_count
                best_name = name

        if best_name is None or best_score <= 0:
            return None
        # 最低命中分阈值：仅靠描述词（权重1）不足以触发，避免"今天天气如何"
        # 命中描述里恰好含"天气"的工具。需要工具名/手工映射级（权重≥3）确认。
        if best_score < 3:
            return None
        tools = getattr(tool_registry, "_tools", {}) or {}
        tool = tools.get(best_name)
        if tool is None:
            return None
        return tool.name, tool.plugin_id

    def _kw_in_text(self, kw: str, text_lower: str) -> bool:
        """关键词匹配：拉丁关键词要求词边界（避免 "app" 命中 "happy"），中文子串即可。"""
        if re.fullmatch(r'[a-z0-9_]+', kw):
            return re.search(
                r'(?<![a-z0-9])' + re.escape(kw) + r'(?![a-z0-9])', text_lower
            ) is not None
        return kw in text_lower

    def _is_question_text(self, text_lower: str) -> bool:
        """疑问句检测：能力询问/是非问句不参与关键词路由，交给 LLM 回答。

        命中以下任一 → True：
        - "吗/么/嘛/呢/？/?" 结尾（"你能暂停音乐吗"/"你会什么"）
        - 独立疑问标记"能不能/可不可以/会不会/知不知道/知道不知道"（无需 吗 结尾）
        - "你能…吗/可以…吗/会…吗/知道…吗"结构（…之间 ≤8 字）
        """
        if not text_lower:
            return False
        if text_lower.endswith(("吗", "么", "嘛", "呢", "？", "?")):
            return True
        if re.search(r"(能不能|可不可以|会不会|知不知道|知道不知道)", text_lower):
            return True
        return _QUESTION_RE.search(text_lower) is not None

    @staticmethod
    def _is_keyword_whitelisted(tool) -> bool:
        """判断工具是否允许参与关键词路由（白名单内确定性安全工具）。

        白名单：hanako-audio-player 的 audio_bus 控制类（工具名）+ linjian-peek
        手机控制工具（插件 ID，oc-pet 本地插件）。其余 Hanako 插件工具不建索引，
        让位 Hanako LLM 语义选工具。
        """
        name = getattr(tool, "name", "") or ""
        plugin_id = getattr(tool, "plugin_id", "") or ""
        return name in _KEYWORD_ROUTE_TOOL_WHITELIST or plugin_id in _KEYWORD_ROUTE_PLUGIN_WHITELIST

    # ── 执行与结果构造 ──────────────────────────────────────

    def _build_tool_result(self, hit: Tuple[str, str], tool_registry,
                           execute: bool, match_kind: str) -> RouteResult:
        """构造插件工具的 RouteResult（capability=工具名，text=执行结果文案）。"""
        tool_name, plugin_id = hit
        tool = tool_registry.get_tool(tool_name) if hasattr(tool_registry, "get_tool") else None
        if tool is None:
            tools = getattr(tool_registry, "_tools", {}) or {}
            tool = tools.get(tool_name)
        if tool is None:
            return RouteResult(
                capability=tool_name,
                text=f"找不到工具 {tool_name}",
                emotion="sad",
                anim="idle",
            )

        emotion, anim = self._infer_emotion_anim(tool_name)

        # 不执行 → 返回"工具执行中"标记（由上层异步执行/展示进度）
        if not execute or self._tool_executor is None:
            return RouteResult(
                capability=tool_name,
                text=f"正在执行工具 {tool_name}…",
                emotion=emotion,
                anim=anim,
            )

        # 执行工具（快速路由不带参数，参数由 LLM 工具调用路径处理）
        try:
            result_text = self._tool_executor.execute(tool, {})
            logger.info("统一路由(%s) 执行工具 %s: %s",
                        match_kind, tool_name, str(result_text or "")[:100])
        except Exception as e:
            logger.warning("统一路由执行工具 %s 失败: %s", tool_name, e)
            result_text = f"操作失败：{e}"

        result_str = str(result_text or "").strip()
        if not result_str:
            result_str = "执行完成"

        return RouteResult(
            capability=tool_name,
            text=result_str[:200],
            emotion=emotion,
            anim=anim,
            tool_result=result_str,
        )

    def _infer_emotion_anim(self, tool_name: str) -> Tuple[str, str]:
        """从工具名推断执行后的情绪/动画。"""
        name = tool_name.lower()
        if "alarm" in name or "notif" in name or "open" in name or "home" in name:
            return "happy", "extra"
        if "error" in name or "fail" in name or "denied" in name:
            return "sad", "idle"
        if "peek" in name or "screen" in name or "state" in name or "status" in name:
            return "neutral", "idle"
        return "neutral", "idle"

    # ── 热刷新 ──────────────────────────────────────────────

    def refresh(self, tool_registry=None) -> None:
        """工具变更后重建关键词索引（供热刷新调用）。

        注意：只重建索引，不负责重新扫描插件目录——调用方需先让
        ToolRegistry.refresh() 重新 discover（见 conversation_engine._hot_refresh_tools）。
        """
        reg = tool_registry or self._tool_registry
        if reg is None:
            self._keyword_index = {}
            self._last_refresh = time.monotonic()
            return
        self._tool_registry = reg
        self._build_keyword_index(reg)

    def should_refresh(self, interval: float = 30.0) -> bool:
        """距上次刷新超过 interval 秒 → 应触发热刷新。"""
        if interval <= 0:
            return False
        return (time.monotonic() - self._last_refresh) >= interval

    # ── 索引构建 ────────────────────────────────────────────

    def _build_keyword_index(self, tool_registry) -> None:
        """为每个**白名单内** ToolDef 从 name + description 构建关键词表（模块级缓存 + 每次 discover 后刷新）。

        R3 白名单模式：非白名单 Hanako 插件工具不参与关键词匹配（让位 Hanako LLM），
        不写入索引。
        """
        index: dict[str, dict[str, int]] = {}
        tools = getattr(tool_registry, "_tools", {}) or {}
        skipped = 0
        for name, tool in tools.items():
            if not self._is_keyword_whitelisted(tool):
                skipped += 1
                continue
            kws: dict[str, int] = {}

            # 1. 工具名整体（归一化，去掉分隔符）
            norm = self._normalize_name(name)
            if norm:
                kws[norm] = max(kws.get(norm, 0), 5)

            # 2. 工具名拆分 token + 中文同义词
            for token in self._split_name_tokens(name):
                t = token.lower()
                if t:
                    kws[t] = max(kws.get(t, 0), 3)
                zh = _EN_TO_ZH.get(t)
                if zh:
                    kws[zh] = max(kws.get(zh, 0), 3)

            # 3. 手工关键词映射（插件少，手工维护 + name 拆分兜底）
            for k in _TOOL_KEYWORD_MAP.get(name, []):
                kk = k.lower()
                if kk:
                    kws[kk] = max(kws.get(kk, 0), 3)

            # 4. 描述中的常见中文词（权重低，防误伤）
            desc = tool.description or ""
            for w in _COMMON_ZH_WORDS:
                if w in desc:
                    kws[w] = max(kws.get(w, 0), 1)

            index[name] = kws
        self._keyword_index = index
        self._last_refresh = time.monotonic()
        logger.debug("统一路由关键词索引: %d 个工具（跳过 %d 个非白名单 Hanako 工具）",
                     len(index), skipped)

    @staticmethod
    def _normalize_name(name: str) -> str:
        """工具名归一化：小写 + 去掉所有非字母数字（下划线/连字符等价）。"""
        return re.sub(r'[^a-z0-9]', '', str(name).lower())

    @staticmethod
    def _split_name_tokens(name: str) -> list[str]:
        """工具名按非字母数字切分：phone_peek_screen → [phone, peek, screen]"""
        return [t for t in re.split(r'[^a-zA-Z0-9]+', name) if t]
