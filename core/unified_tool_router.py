"""统一工具调度层 — 本地能力 / 本地插件工具 / Hanako 服务端工具 三类来源统一快速路由

背景：oc-pet 桌宠现有三层能力
1. 本地能力  core/capability_registry.py：CAPABILITIES 静态列表（15 个：放歌/日报/截图/搜索…）
2. 本地插件工具 core/tool_registry.py：Hanako 全局插件 + oc-pet 本地插件（如 linjian-peek 手机控制 8 工具）
3. Hanako 服务端工具：WS 模式（M4）下工具由服务端执行，桌宠不传 tools

本层把它们统一到一个快速路由，路由顺序（快→慢）：
1. 静态能力优先：先试 CapabilityRouter（15 个内置能力，如"放首歌"→play_music）
2. 显式指定：文本含"用X"/"调用X"/"使用X"/"X工具" → 精确匹配插件工具名
3. 关键词命中：工具名/描述关键词匹配用户文本 → 直达插件工具
4. 兜底：返回 None（交给 LLM 工具调用 / Hanako 服务端）

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
    r'(?:用|调用|使用|让|请|帮我)?\s*([\w-]+)\s*(?:工具|插件|来做|去|执行)?'
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

# 描述里常见中文名词：从 ToolDef.description 提取关键词（权重低，防误伤）
_COMMON_ZH_WORDS = [
    "手机", "状态", "截图", "屏幕", "控制", "通知", "闹钟", "应用",
    "桌面", "主页", "电量", "信息", "返回", "打开", "启动", "查看",
    "读取", "发送", "设备", "服务", "权限", "图片", "文字", "前台",
    "当前", "最近", "订阅", "待办", "任务", "搜索", "音乐", "网页",
    "天气", "邮件", "闹钟", "章节", "项目", "阅读", "播放", "暂停",
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
}


class UnifiedToolRouter:
    """统一工具调度层：三类工具来源快速路由。

    路由顺序（快→慢）：
    1. 静态能力优先：先试 static_router（CapabilityRouter，15 个内置能力）
    2. 显式指定：文本含"用X"/"调用X"/"使用X"/"X工具" → 精确匹配插件工具名
    3. 关键词命中：工具名/描述关键词匹配用户文本 → 直达插件工具
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
        """统一路由：静态能力 → 插件工具（显式/关键词）→ None（兜底 LLM）

        Args:
            text: 用户文本（文字或语音转写后的文本）
            tool_registry: ToolRegistry（本地插件工具）
            static_router: CapabilityRouter（15 个静态能力，优先）
            execute: 命中插件工具后是否立即执行（False → 返回"工具执行中"标记）

        Returns:
            RouteResult 命中；None 需回退到 LLM / Hanako 服务端
        """
        if not text or not text.strip():
            return None
        text = text.strip()

        # 1. 静态能力优先（15 个内置能力，如 放歌/日报/截图/搜索）
        if static_router is not None:
            try:
                result = static_router.route(text)
                if result is not None:
                    logger.info("统一路由 → 静态能力: %s", result.capability)
                    return result
            except Exception as e:
                logger.warning("统一路由：静态能力异常: %s", e)

        reg = tool_registry or self._tool_registry
        if reg is None:
            return None

        # 2. 插件工具：显式指定（"用X"/"调用X"/"使用X"/"X工具"）
        hit = self._match_explicit(text, reg)
        if hit is not None:
            logger.info("统一路由 → 显式指定: %s (plugin=%s)", hit[0], hit[1])
            return self._build_tool_result(hit, reg, execute, "explicit")

        # 3. 插件工具：关键词命中（工具名/描述关键词）
        hit = self._match_keyword(text, reg)
        if hit is not None:
            logger.info("统一路由 → 关键词命中: %s (plugin=%s)", hit[0], hit[1])
            return self._build_tool_result(hit, reg, execute, "keyword")

        # 4. 兜底：交给 LLM 工具调用 / Hanako 服务端
        return None

    # ── 二级：显式指定 ──────────────────────────────────────

    def _match_explicit(self, text: str, tool_registry) -> Optional[Tuple[str, str]]:
        """解析显式指定：返回 (tool_name, plugin_id) 或 None

        正则提取候选 token → 与工具名精确匹配（忽略大小写、去下划线/连字符）。
        """
        token = self._parse_explicit_token(text)
        if not token:
            return None
        norm = self._normalize_name(token)
        if not norm:
            return None

        tools = getattr(tool_registry, "_tools", {}) or {}
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
        """关键词路由：工具名/描述关键词匹配用户文本。

        打分：工具名整体(5) > 工具名 token+同义词(3) = 手工映射(3) > 描述词(1)。
        取最高分工具；同分取关键词命中数更多者。
        """
        text_lower = text.lower()
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
        """为每个 ToolDef 从 name + description 构建关键词表（模块级缓存 + 每次 discover 后刷新）。"""
        index: dict[str, dict[str, int]] = {}
        tools = getattr(tool_registry, "_tools", {}) or {}
        for name, tool in tools.items():
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
        logger.debug("统一路由关键词索引: %d 个工具", len(index))

    @staticmethod
    def _normalize_name(name: str) -> str:
        """工具名归一化：小写 + 去掉所有非字母数字（下划线/连字符等价）。"""
        return re.sub(r'[^a-z0-9]', '', str(name).lower())

    @staticmethod
    def _split_name_tokens(name: str) -> list[str]:
        """工具名按非字母数字切分：phone_peek_screen → [phone, peek, screen]"""
        return [t for t in re.split(r'[^a-zA-Z0-9]+', name) if t]
