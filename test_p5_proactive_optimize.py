"""P5 主动对话优化测试：即时化 + 不走 Hanako + 窗口名传入视觉模型

覆盖 P5 验收的客观项：
- proactive 触发直接弹场景文案气泡（不依赖 LLM mock 返回、不调 engine.send）
- 场景 → 气泡 emotion 映射正确（thinking/happy/surprised/neutral）
- chat() 路由器：proactive/idle 走 chat_direct，不碰 Hanako session
- chat_direct 对 proactive/idle 加 [source] 前缀（已有行为保持）
- 视觉模型提示词拼接窗口名（app+title → 含窗口名；无窗口名 → 不拼）
- 回归：user 消息仍走 Hanako

运行: python -m pytest test_p5_proactive_optimize.py -v --basetemp=.pytest_tmp
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pet_mixins.behavior_mixin import BehaviorMixin
from core.perception.scenarios import (
    get_bubble_emotion_for_prompt,
    SCENARIO_BUBBLE_EMOTION,
)
from core.perception.screen import build_vision_prompt, VISION_PROMPT
from core.harness_adapter import HanakoPetAdapter


# ── 工具：构造最小可用的 mock 桌宠 / 适配器 ──

def _make_pet():
    """构造不带 Qt 的 BehaviorMixin 实例（renderer/perception 用 MagicMock）。"""
    pet = BehaviorMixin.__new__(BehaviorMixin)
    pet._engine = MagicMock()
    pet._renderer = MagicMock()
    pet._renderer._model = MagicMock()
    pet._renderer._emotion_motion_cooldown = {}
    pet._renderer._last_gesture_at = 0.0
    pet._renderer._play_motion_kw = MagicMock(return_value=True)
    pet._renderer._note_motion_started = MagicMock()
    pet._renderer.set_emotion_expression_only = MagicMock()
    pet._perception = MagicMock()
    pet._perception.proactive = MagicMock()
    pet._show_bubble = MagicMock()
    pet._set_anim_seq = MagicMock()
    return pet


def _make_router_adapter(transport_mode="prefer_hanako"):
    """构造 chat() 路由器测试用的适配器（chat_via_hanako/chat_direct 已 mock）。"""
    a = HanakoPetAdapter.__new__(HanakoPetAdapter)
    a.transport_mode = transport_mode
    a._session_manager = MagicMock()
    a.chat_via_hanako = MagicMock(return_value=("hanako", "happy"))
    a.chat_direct = MagicMock(return_value=("direct", "neutral"))
    return a


def _make_direct_adapter():
    """构造 chat_direct 实现测试用的适配器（_call_api 已 mock）。"""
    a = HanakoPetAdapter.__new__(HanakoPetAdapter)
    a.transport_mode = "direct"
    a._session_manager = None
    a._current_session = None
    a._history = []
    a._base_url = "http://example.com"
    a._api_key = "key"
    a._model = "model"
    a._api_type = "openai-completions"
    a._system_prompt = "system"
    a._memory_budget = 800
    a._context = MagicMock()
    a._context.build_memory_context.return_value = ""
    a._call_api = MagicMock(return_value="好的[emotion:happy]")
    return a


# ── 1. proactive 即时化：直接弹文案，LLM 不参与 ──

def test_proactive_trigger_shows_copy_directly_no_llm():
    """proactive 触发直接显示场景文案（不依赖 LLM mock 返回，不调 engine.send）"""
    pet = _make_pet()
    text = "写了这么久，休息一下吧？我陪你说说话。"
    pet._on_proactive_trigger(text)

    # 直接显示文案（不是"思考中"、不是"[主动对话触发]"指令包装）
    pet._show_bubble.assert_called_once()
    args, kwargs = pet._show_bubble.call_args
    assert args[0] == text, f"气泡应直接显示文案, 实际 {args[0]!r}"
    assert "思考中" not in args[0], "不应显示思考中气泡"
    assert kwargs.get("emotion") == "thinking", f"长时间工作场景应 thinking, 实际 {kwargs.get('emotion')}"

    # 不再调 engine.send（LLM 不参与 proactive 路径）
    pet._engine.send.assert_not_called()

    # 记录对话空闲计时（proactive 冷却正确）
    pet._perception.proactive.mark_conversation.assert_called_once()

    # 保留 waving 动作触发逻辑
    pet._renderer._play_motion_kw.assert_called_once_with("waving")
    pet._renderer._note_motion_started.assert_called_once_with("proactive", is_idle=False)
    pet._set_anim_seq.assert_called_once()


def test_proactive_trigger_unknown_prompt_default_neutral():
    """未知文案 → neutral 情绪，仍直接弹气泡"""
    pet = _make_pet()
    pet._on_proactive_trigger("完全陌生的文案")
    pet._show_bubble.assert_called_once()
    _, kwargs = pet._show_bubble.call_args
    assert kwargs.get("emotion") == "neutral"
    pet._engine.send.assert_not_called()


# ── 2. 场景 → 气泡 emotion 映射 ──

def test_scenario_bubble_emotion_mapping():
    """映射表：深夜/长时工作→thinking，游戏/视频→happy，切窗→surprised"""
    assert SCENARIO_BUBBLE_EMOTION["late_night_work"] == "thinking"
    assert SCENARIO_BUBBLE_EMOTION["long_work_break"] == "thinking"
    assert SCENARIO_BUBBLE_EMOTION["gaming"] == "happy"
    assert SCENARIO_BUBBLE_EMOTION["video_watching"] == "happy"
    assert SCENARIO_BUBBLE_EMOTION["window_switch"] == "surprised"


def test_bubble_emotion_resolved_from_scenario_text():
    """场景文案反查 → 正确情绪"""
    assert get_bubble_emotion_for_prompt("都这么晚了还在忙呀…先歇口气，我陪着你。") == "thinking"  # late_night_work
    assert get_bubble_emotion_for_prompt("这波操作我可以看一天！") == "happy"  # gaming
    assert get_bubble_emotion_for_prompt("在看什么好东西？好看吗～") == "happy"  # video_watching
    assert get_bubble_emotion_for_prompt("是不是卡住了？还是找不到东西啦？") == "surprised"  # window_switch


def test_bubble_emotion_rule_prompt_matches_prefix():
    """规则引擎文案（场景文案前缀/子串）也能容错匹配"""
    # DEFAULT_RULES 的 "写了这么久，休息一下吧？" 是 long_work_break 文案的前缀
    assert get_bubble_emotion_for_prompt("写了这么久，休息一下吧？") == "thinking"


def test_bubble_emotion_unknown_default_neutral():
    """未知/空文案 → neutral 兜底"""
    assert get_bubble_emotion_for_prompt("完全陌生的文案") == "neutral"
    assert get_bubble_emotion_for_prompt("") == "neutral"
    assert get_bubble_emotion_for_prompt(None) == "neutral"


# ── 3. chat() 路由器：proactive/idle 走 chat_direct，不碰 Hanako ──

def test_chat_proactive_uses_chat_direct_not_hanako():
    """proactive 消息走 chat_direct；即使 session_manager 已注入也不碰 Hanako"""
    a = _make_router_adapter(transport_mode="prefer_hanako")
    result = a.chat("proactive msg", source="proactive")

    assert result == ("direct", "neutral")
    a.chat_direct.assert_called_once_with("proactive msg", False, "", tools=None, source="proactive")
    a.chat_via_hanako.assert_not_called()
    a._session_manager.send_and_wait.assert_not_called()


def test_chat_idle_uses_chat_direct_not_hanako():
    """idle 消息同样走 chat_direct，不碰 Hanako"""
    a = _make_router_adapter(transport_mode="prefer_hanako")
    result = a.chat("idle msg", source="idle")

    assert result == ("direct", "neutral")
    a.chat_direct.assert_called_once_with("idle msg", False, "", tools=None, source="idle")
    a.chat_via_hanako.assert_not_called()


# ── 4. chat_direct 对 proactive/idle 加 [source] 前缀（已有行为保持）──

def test_chat_direct_adds_proactive_prefix():
    """chat_direct 对 proactive 消息加 [proactive] 前缀"""
    a = _make_direct_adapter()
    reply, emotion = a.chat_direct("hello", False, "", None, source="proactive")

    messages = a._call_api.call_args[0][0]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "[proactive] hello", f"应加 [proactive] 前缀, 实际 {messages[-1]['content']!r}"
    assert reply == "好的"
    assert emotion == "happy"


def test_chat_direct_adds_idle_prefix():
    """chat_direct 对 idle 消息加 [idle] 前缀"""
    a = _make_direct_adapter()
    a.chat_direct("hello", False, "", None, source="idle")

    messages = a._call_api.call_args[0][0]
    assert messages[-1]["content"] == "[idle] hello"


def test_chat_direct_user_no_prefix():
    """chat_direct 对 user 消息不加前缀（不破坏现有 prompt 结构）"""
    a = _make_direct_adapter()
    a.chat_direct("hello", False, "", None, source="user")

    messages = a._call_api.call_args[0][0]
    assert messages[-1]["content"] == "hello"


# ── 5. 视觉模型提示词拼接窗口名 ──

def test_vision_prompt_with_window_name():
    """app+title → vision_text 含窗口名，且加在 JSON 格式要求之后"""
    text = build_vision_prompt(app="chrome.exe", title="Bilibili - 首页")

    assert "chrome.exe" in text
    assert "Bilibili - 首页" in text
    assert "[当前窗口]" in text
    assert "进程=chrome.exe" in text
    assert "标题=Bilibili - 首页" in text
    # JSON 格式约束（"只返回 JSON"）必须仍在窗口提示之前
    assert text.index("只返回 JSON，不要其他文字") < text.index("[当前窗口]")


def test_vision_prompt_with_only_app():
    """只有进程名（无标题）→ 标题显示'未知'"""
    text = build_vision_prompt(app="code.exe", title="")
    assert "进程=code.exe" in text
    assert "标题=未知" in text


def test_vision_prompt_without_window_name_unchanged():
    """无窗口名 → 原样返回 VISION_PROMPT，不拼接"""
    assert build_vision_prompt(app="", title="") == VISION_PROMPT
    assert build_vision_prompt() == VISION_PROMPT
    assert "[当前窗口]" not in VISION_PROMPT


# ── 6. 回归：user 消息仍走 Hanako ──

def test_chat_user_still_uses_hanako():
    """user 消息仍走 Hanako session（不破坏现有路径）"""
    a = _make_router_adapter(transport_mode="prefer_hanako")
    result = a.chat("user msg", source="user")

    assert result == ("hanako", "happy")
    a.chat_via_hanako.assert_called_once()
    a.chat_direct.assert_not_called()


def test_chat_user_direct_mode_uses_chat_direct():
    """direct 模式下 user 消息走 chat_direct（原行为保持）"""
    a = _make_router_adapter(transport_mode="direct")
    result = a.chat("user msg", source="user")

    assert result == ("direct", "neutral")
    a.chat_direct.assert_called_once()
    a.chat_via_hanako.assert_not_called()
