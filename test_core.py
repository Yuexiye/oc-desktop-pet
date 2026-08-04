"""单元测试 - 核心逻辑（不依赖 Qt）

运行: python -m pytest test_core.py -v
"""
import time
from unittest.mock import patch

from core.perception import (
    EmotionStateMachine, TimePerception, ProactiveScheduler
)
from core.hanako_monitor import compact_bubble_text


# ════════════════════════════════════════════════════════════
#  EmotionStateMachine
# ════════════════════════════════════════════════════════════

class TestEmotionStateMachine:

    def test_initial_state(self):
        sm = EmotionStateMachine()
        assert sm.current == "neutral"
        assert sm.intensity == 0.0
        assert not sm.should_show_emotion()

    def test_trigger_sets_emotion(self):
        sm = EmotionStateMachine()
        sm.trigger("happy")
        assert sm.current == "happy"
        assert sm.intensity == 1.0
        assert sm.should_show_emotion()

    def test_neutral_trigger_ignored(self):
        sm = EmotionStateMachine()
        sm.trigger("happy")
        sm.trigger("neutral")
        assert sm.current == "happy"  # neutral 不覆盖

    def test_decay_over_time(self):
        sm = EmotionStateMachine()
        sm.trigger("happy", intensity=1.0)
        # 模拟 10 分钟过去
        sm._last_trigger = time.time() - 600
        sm.tick()
        # 10 分钟 * 8%/分钟 = 80% 衰减
        assert sm.intensity < 0.3
        assert sm.intensity > 0.0

    def test_full_decay_to_neutral(self):
        sm = EmotionStateMachine()
        sm.trigger("happy", intensity=1.0)
        # 模拟 15 分钟过去（超过 100% 衰减）
        sm._last_trigger = time.time() - 900
        sm.tick()
        assert sm.current == "neutral"
        assert sm.intensity == 0.0

    def test_reset(self):
        sm = EmotionStateMachine()
        sm.trigger("angry")
        sm.reset()
        assert sm.current == "neutral"
        assert sm.intensity == 0.0

    def test_format_for_prompt(self):
        sm = EmotionStateMachine()
        assert sm.format_for_prompt() == ""
        sm.trigger("happy")
        result = sm.format_for_prompt()
        assert "happy" in result
        assert "100%" in result

    def test_thread_safety(self):
        """并发 trigger + tick 不崩溃"""
        import threading
        sm = EmotionStateMachine()
        errors = []

        def trigger_loop():
            for _ in range(100):
                try:
                    sm.trigger("happy")
                except Exception as e:
                    errors.append(e)

        def tick_loop():
            for _ in range(100):
                try:
                    sm.tick()
                except Exception as e:
                    errors.append(e)

        t1 = threading.Thread(target=trigger_loop)
        t2 = threading.Thread(target=tick_loop)
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert not errors


# ════════════════════════════════════════════════════════════
#  TimePerception
# ════════════════════════════════════════════════════════════

class TestTimePerception:

    def test_returns_valid_period(self):
        tp = TimePerception()
        ctx = tp.get_context()
        assert ctx["period"] in ("morning", "noon", "afternoon", "evening", "late_night", "midnight")
        assert 0 <= ctx["hour"] < 24
        assert 0 <= ctx["weekday"] < 7
        assert isinstance(ctx["is_weekend"], bool)

    def test_format_for_prompt(self):
        tp = TimePerception()
        result = tp.format_for_prompt()
        assert "当前时间" in result
        assert "周末" in result or "工作日" in result


# ════════════════════════════════════════════════════════════
#  ProactiveScheduler
# ════════════════════════════════════════════════════════════

class TestProactiveScheduler:

    def test_disabled_does_not_trigger(self):
        sched = ProactiveScheduler(on_proactive=lambda t: None)
        sched.disable()
        assert sched.tick() is None

    def test_cooldown_blocks_trigger(self):
        sched = ProactiveScheduler(on_proactive=lambda t: None)
        sched.load_config({"rules": [{"idle_min": 0, "foreground": ["*"], "prompt": "test", "weight": 1.0}]})
        # 设置冷却
        sched._cooldown_until = time.time() + 999
        assert sched.tick() is None

    def test_short_idle_does_not_trigger(self):
        sched = ProactiveScheduler(on_proactive=lambda t: None)
        sched.load_config({"rules": [{"idle_min": 5, "foreground": ["*"], "prompt": "test", "weight": 1.0}]})
        # 模拟 60 秒空闲（< 180 秒阈值）
        import ctypes
        orig_get_last_input = ctypes.windll.user32.GetLastInputInfo
        orig_get_tick = ctypes.windll.kernel32.GetTickCount
        try:
            ctypes.windll.kernel32.GetTickCount.return_value = 60000
            assert sched.tick() is None
        finally:
            ctypes.windll.user32.GetLastInputInfo = orig_get_last_input
            ctypes.windll.kernel32.GetTickCount = orig_get_tick

    def test_rule_match_triggers(self):
        triggered = []
        sched = ProactiveScheduler(on_proactive=lambda t: triggered.append(t))
        sched.load_config({"rules": [{"idle_min": 0, "foreground": ["*"], "prompt": "hi", "weight": 1.0}]})
        # 模拟 200 秒空闲（> 180 秒阈值）
        # 直接 patch _get_idle_seconds 太复杂，改为直接设置内部状态
        # 验证规则匹配逻辑：idle_min=0 + weight=1.0 + foreground=*
        # 只要 idle > 180 就触发
        import ctypes
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        lii.dwTime = 0  # 很久没输入
        orig = ctypes.windll.user32.GetLastInputInfo
        try:
            ctypes.windll.user32.GetLastInputInfo = lambda ptr: True
            ctypes.windll.kernel32.GetTickCount = lambda: 200000
            result = sched.tick()
            assert result == "hi"
            assert triggered == ["hi"]
        finally:
            ctypes.windll.user32.GetLastInputInfo = orig

    def test_enable_disable(self):
        sched = ProactiveScheduler()
        assert sched.enabled
        sched.disable()
        assert not sched.enabled
        sched.enable()
        assert sched.enabled

    def test_reset_sets_cooldown(self):
        sched = ProactiveScheduler()
        sched.load_config({"cooldown_minutes": 5})
        before = time.time()
        sched.reset()
        assert sched._cooldown_until > before


# ════════════════════════════════════════════════════════════
#  compact_bubble_text
# ════════════════════════════════════════════════════════════

class TestCompactBubbleText:

    def test_short_text_unchanged(self):
        assert compact_bubble_text("你好") == "你好"

    def test_long_text_truncated(self):
        long = "这是一段很长的文字" * 20
        result = compact_bubble_text(long)
        assert len(result) < len(long)

    def test_none_input(self):
        assert compact_bubble_text("") == ""

    def test_sentence_split(self):
        text = "第一句话。第二句话。第三句话。"
        result = compact_bubble_text(text)
        # compact_bubble_text 取最后一句
        assert "句话" in result
        assert len(result) <= len(text)


# ════════════════════════════════════════════════════════════
#  _extract_json_object（视觉感知 JSON 提取）
# ════════════════════════════════════════════════════════════

class TestExtractJsonObject:

    def _extract(self, text):
        from core.perception.screen import _extract_json_object
        return _extract_json_object(text)

    def test_plain_json(self):
        assert self._extract('{"activity": "a", "category": "work"}')["category"] == "work"

    def test_nested_json(self):
        r = self._extract('{"detail": {"apps": ["vscode"], "state": {"ok": true}}}')
        assert r["detail"]["apps"] == ["vscode"]
        assert r["detail"]["state"]["ok"] is True

    def test_wrapped_with_text(self):
        r = self._extract('分析：{"summary": "写代码"} 结束')
        assert r["summary"] == "写代码"

    def test_brace_inside_string(self):
        r = self._extract('{"text": "a {b} c", "x": 1}')
        assert r["text"] == "a {b} c"

    def test_no_json(self):
        assert self._extract("纯文字") is None
        assert self._extract("") is None

    def test_first_of_multiple(self):
        assert self._extract('{"a": 1} 后 {"b": 2}') == {"a": 1}


# ════════════════════════════════════════════════════════════
#  map_emotion_to_anim（情绪→动画映射一致性）
# ════════════════════════════════════════════════════════════

class TestEmotionToAnim:

    def test_matches_expression_map(self):
        """map_emotion_to_anim 必须与 config.EXPRESSION_MAP 权威映射一致"""
        from core.conversation_engine import map_emotion_to_anim
        from config import EXPRESSION_MAP
        for emo in EXPRESSION_MAP:
            expected = EXPRESSION_MAP[emo][0] or "idle"
            assert map_emotion_to_anim(emo) == expected, f"{emo} 不一致"

    def test_unknown_emotion_falls_back_to_idle(self):
        from core.conversation_engine import map_emotion_to_anim
        assert map_emotion_to_anim("nonexistent_emotion") == "idle"


# ════════════════════════════════════════════════════════════
#  HanakoMonitor agent 过滤（一桌宠一助手）
# ════════════════════════════════════════════════════════════

class _FakeSession:
    def __init__(self, sid, agent):
        self.session_id = sid
        self.session_path = f"/agents/{agent}/sessions/{sid}.jsonl"
        self.agent_id = agent


class _FakeSM:
    def __init__(self, sessions):
        self._sessions = sessions

    def _session_for_event(self, event):
        sid = event.get("sessionId")
        for s in self._sessions:
            if s.session_id == sid:
                return s
        return None


class TestHanakoMonitorAgentFilter:

    def _make_monitor(self, agent_id, sessions):
        from core.hanako_monitor import HanakoMonitor
        mon = HanakoMonitor()
        mon.set_agent_context(agent_id, _FakeSM(sessions))
        return mon

    def test_own_agent_event_shown(self):
        sessions = [_FakeSession("sess_yue", "yuexinmiao"), _FakeSession("sess_gla", "glados")]
        mon = self._make_monitor("yuexinmiao", sessions)
        called = []
        mon._on_state_change = lambda *a, **k: called.append(a)
        mon.push_event({"type": "thinking_start", "sessionId": "sess_yue"})
        assert called, "own agent event should be shown"

    def test_other_agent_event_filtered(self):
        sessions = [_FakeSession("sess_yue", "yuexinmiao"), _FakeSession("sess_gla", "glados")]
        mon = self._make_monitor("yuexinmiao", sessions)
        called = []
        mon._on_state_change = lambda *a, **k: called.append(a)
        mon.push_event({"type": "thinking_start", "sessionId": "sess_gla"})
        assert not called, "other agent event should be filtered"

    def test_no_agent_bound_passes(self):
        """未绑定 agent_id 时不过滤（向后兼容）"""
        from core.hanako_monitor import HanakoMonitor
        mon = HanakoMonitor()
        called = []
        mon._on_state_change = lambda *a, **k: called.append(a)
        mon.push_event({"type": "thinking_start", "sessionId": "sess_gla"})
        assert called, "no-agent bound should pass through"

    def test_agent_inferred_from_session_path(self):
        """session_manager 映射不到 agent_id 时，从 session_path 推断"""
        from core.hanako_monitor import HanakoMonitor
        mon = HanakoMonitor()
        mon.set_agent_context("yuexinmiao", None)  # 无 session_manager
        assert mon._event_belongs_to_agent({
            "type": "thinking_start",
            "sessionPath": "/agents/yuexinmiao/sessions/abc.jsonl"
        }) is True
        assert mon._event_belongs_to_agent({
            "type": "thinking_start",
            "sessionPath": "/home/u/.hanako/agents/glados/sessions/abc.jsonl"
        }) is False
        assert mon._event_belongs_to_agent({"type": "thinking_start"}) is True


# ════════════════════════════════════════════════════════════
#  P1 打断状态机（消息代际）
# ════════════════════════════════════════════════════════════

class _FakeAdapter:
    """模拟 LLM 适配器：阻塞一段时间后返回，可统计调用次数"""
    def __init__(self, delay=0.3):
        self.calls = 0
        self.delay = delay

    def chat(self, message, inject_memory=True, extra_context="", tools=None, source="user"):
        self.calls += 1
        import time
        time.sleep(self.delay)
        return message, "happy"


class TestInterruptStateMachine:

    def _eng(self):
        from core.conversation_engine import ConversationEngine
        eng = ConversationEngine(character_id="test")
        eng._adapter = _FakeAdapter()
        eng._tts = None
        return eng

    def test_normal_message_callback(self):
        """正常处理（未打断）应回调结果"""
        eng = self._eng()
        replies = []
        eng.on_reply = lambda r, e, a, p: replies.append(r)
        eng._process_message({"text": "hi", "character": "test", "source": "user", "gen": 1})
        assert replies == ["hi"], f"正常应回调, got {replies}"

    def test_interrupt_discards_inflight(self):
        """LLM 调用中被打断 → 结果被丢弃（不回调）"""
        import threading, time
        eng = self._eng()
        replies = []
        eng.on_reply = lambda r, e, a, p: replies.append(r)
        eng._generation = 1  # 模拟 send 后
        msg = {"text": "hi", "character": "test", "source": "user", "gen": 1}
        t = threading.Thread(target=lambda: eng._process_message(msg), daemon=True)
        t.start()
        time.sleep(0.1)  # LLM 调用中
        state = eng.interrupt(reason="voice_start")  # 打断, generation -> 2
        t.join(timeout=1)
        assert eng._adapter.calls == 1, "LLM 应被调用过一次"
        assert replies == [], f"打断后结果应丢弃, got {replies}"
        assert state == "interrupted"

    def test_interrupt_state_mapping(self):
        """打断原因 → 状态映射"""
        eng = self._eng()
        assert eng.interrupt(reason="new_message") == "cancelled"
        assert eng.interrupt(reason="voice_start") == "interrupted"
        assert eng.interrupt(reason="user_stop") == "interrupted"

    def test_stale_after_interrupt(self):
        """打断后旧消息代际过期"""
        eng = self._eng()
        eng._generation = 1
        assert eng._is_stale(0) is True   # 旧代际过期
        assert eng._is_stale(1) is False  # 当前代际有效
        eng.interrupt(reason="user_stop")
        assert eng._is_stale(1) is True   # 打断后过期
        assert eng._is_stale(2) is False
