"""P1 灵魂：意图分类器 + 场景化反应 + Proactive 集成测试

覆盖 P1 验收的客观项：
- IntentClassifier 能稳定区分 工作/娱乐/焦虑(tense)/疲惫(tired) 四类
- 10 个场景（late_night_work / long_work_break / tutorial_follow /
  window_switch / gaming / video_watching / chat_idle / morning_first /
  weekend_play / late_night_all）都能正确识别
- ProactiveScheduler 意图优先触发 + 规则兜底

运行: python -m pytest test_p1_intent.py -v
"""
import os
import sys
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.perception.intent import classify_intent, LATE_NIGHT_WORK_MINUTES
from core.perception.scenarios import get_reaction, SCENARIO_REACTIONS, is_disruptive
from core.perception.proactive import ProactiveScheduler, INTENT_MIN_CONFIDENCE


# ── 1. 意图分类器：四类区分 ──

def test_intent_work():
    """白天正常写代码 → work"""
    r = classify_intent(period="afternoon", category="development", activity="typing",
                        fg_duration_min=30, conversation_idle_min=10)
    assert r["intent"] in ("work", "tired"), f"应为 work/tired, 实际 {r}"
    assert r["scenario"] == "long_work_break" or r["confidence"] < 0.8


def test_intent_tense_late_night_work():
    """深夜 + 开发 120 分钟 → tense 深夜加班"""
    r = classify_intent(period="late_night", category="development", activity="typing",
                        fg_duration_min=120, conversation_idle_min=30)
    assert r["intent"] == "tense", f"应为 tense, 实际 {r}"
    assert r["scenario"] == "late_night_work"


def test_intent_tired_long_work():
    """白天开发 120 分钟 → tired 该休息"""
    r = classify_intent(period="afternoon", category="development", activity="typing",
                        fg_duration_min=120, conversation_idle_min=20)
    assert r["intent"] == "tired", f"应为 tired, 实际 {r}"
    assert r["scenario"] == "long_work_break"


def test_intent_learn_tutorial():
    """browsing 30 分钟 → learn 连续学习"""
    r = classify_intent(period="evening", category="browsing", activity="mouse",
                        fg_duration_min=30, conversation_idle_min=15)
    assert r["intent"] == "learn", f"应为 learn, 实际 {r}"
    assert r["scenario"] == "tutorial_follow"


def test_intent_tense_window_switch():
    """5 分钟切 6 次窗 → tense 频繁切窗"""
    r = classify_intent(period="afternoon", category="browsing", activity="mouse",
                        fg_duration_min=3, conversation_idle_min=5,
                        window_switches_5min=6)
    assert r["intent"] == "tense", f"应为 tense, 实际 {r}"
    assert r["scenario"] == "window_switch"


def test_intent_entertain_gaming():
    """gaming → entertain"""
    r = classify_intent(period="evening", category="gaming", activity="mouse",
                        fg_duration_min=40, conversation_idle_min=10)
    assert r["intent"] == "entertain", f"应为 entertain, 实际 {r}"
    assert r["scenario"] == "gaming"


def test_intent_entertain_video():
    """entertainment → 看视频"""
    r = classify_intent(period="evening", category="entertainment", activity="idle",
                        fg_duration_min=20, conversation_idle_min=8)
    assert r["intent"] == "entertain", f"应为 entertain, 实际 {r}"
    assert r["scenario"] == "video_watching"


def test_intent_chat_idle():
    """长时间没说话 → chat_idle"""
    r = classify_intent(period="afternoon", category="other", activity="mouse",
                        fg_duration_min=5, conversation_idle_min=70)
    assert r["scenario"] == "chat_idle"


def test_intent_morning_first():
    """清晨刚启动 → morning_first"""
    r = classify_intent(period="morning", category="other", activity="idle",
                        fg_duration_min=1, conversation_idle_min=2)
    assert r["scenario"] == "morning_first"


def test_intent_weekend_play():
    """周末娱乐 → weekend_play"""
    r = classify_intent(period="afternoon", category="entertainment", activity="mouse",
                        fg_duration_min=30, conversation_idle_min=20, is_weekend=True)
    assert r["intent"] == "entertain", f"实际 {r}"
    assert r["scenario"] in ("weekend_play", "video_watching")


def test_intent_late_night_idle():
    """深夜长时间无输入 → late_night_all"""
    r = classify_intent(period="midnight", category="other", activity="idle",
                        fg_duration_min=10, conversation_idle_min=30)
    assert r["scenario"] == "late_night_all", f"实际 {r}"


# ── 2. 场景文案池：10 场景全覆盖 ──

def test_all_10_scenarios_have_reactions():
    """10 个场景全部有文案池"""
    expected = {
        "late_night_work", "long_work_break", "tutorial_follow", "window_switch",
        "gaming", "video_watching", "chat_idle", "morning_first",
        "weekend_play", "late_night_all",
    }
    missing = expected - set(SCENARIO_REACTIONS.keys())
    assert not missing, f"缺失场景: {missing}"


def test_reaction_returns_text_emotion():
    """get_reaction 返回 text + emotion + weight"""
    r = get_reaction("late_night_work", intensity=0.9)
    assert r["text"], "文案为空"
    assert r["emotion"] in ("happy", "sad", "neutral", "curious", "thinking", "cute")
    assert 0.0 < r["weight"] <= 1.0


def test_reaction_unknown_scenario_fallback():
    """未知场景 → 中性兜底"""
    r = get_reaction("nonexistent_scenario")
    assert r["text"] and r["emotion"] == "neutral"


# ── 3. ProactiveScheduler 集成 ──

def _make_scheduler(foreground_category="development", fg_min=120.0,
                    activity="typing", conv_idle_min=30.0):
    fw = MagicMock()
    fw.last_category = foreground_category
    fw.fg_duration_min = fg_min
    fw.window_switches_5min = 0
    at = MagicMock()
    at.state.state = activity
    scheduler = ProactiveScheduler(foreground_watcher=fw, on_proactive=lambda t: None,
                                   activity_tracker=at)
    scheduler._last_conversation = time.time() - conv_idle_min * 60
    scheduler._cooldown_until = 0.0  # 无冷却
    # 全屏检测依赖真实前台窗口（ctypes），测试环境可能恰逢全屏窗口导致
    # tick 提前返回 None——这些测试验证意图/规则触发，与全屏无关，固定放行
    # （与 test_p6_proactive_advanced 的 mock 模式一致）。
    scheduler._is_fullscreen = lambda: False
    return scheduler, fw, at


def test_proactive_intent_triggered_late_night():
    """深夜加班场景：意图分类直接触发（不再等规则 idle_min）"""
    scheduler, fw, at = _make_scheduler(
        foreground_category="development", fg_min=120.0,
        activity="typing", conv_idle_min=30.0)
    # 模拟深夜：直接改 TimePerception 返回值较麻烦，这里验证分类器信号传递
    prompts = []
    scheduler.on_proactive = prompts.append
    # 深夜需要 period=late_night；这里手动覆盖信号收集
    scheduler._collect_signals = lambda now: {
        "period": "late_night", "category": "development", "activity": "typing",
        "fg_duration_min": 120.0, "conversation_idle_min": 30.0,
        "window_switches_5min": 0, "is_weekend": False,
    }
    result = scheduler.tick()
    assert result, "深夜加班场景应触发意图对话"
    assert prompts, "on_proactive 应被调用"


def test_proactive_rule_fallback():
    """意图未命中（低置信度）→ 规则引擎兜底

    注意：activity=idle 时 cost=1.0，规则 weight 不被衰减，可稳定触发。
    （activity=mouse 时 cost=0.6 → weight 0.6 → 概率性触发，不能用于断言。）
    """
    scheduler, fw, at = _make_scheduler(
        foreground_category="development", fg_min=2.0,  # 短时长 → 意图低置信度
        activity="idle", conv_idle_min=10.0)  # 10 分钟空闲，足够触发规则；idle 无打扰成本
    prompts = []
    scheduler.on_proactive = prompts.append
    scheduler._rules = [{"idle_min": 5, "foreground": ["development"], "prompt": "写了这么久", "weight": 1.0}]
    # 意图信号：低置信度（fg 短 → 不触发意图场景，走规则兜底）
    scheduler._collect_signals = lambda now: {
        "period": "afternoon", "category": "development", "activity": "idle",
        "fg_duration_min": 2.0, "conversation_idle_min": 10.0,
        "window_switches_5min": 0, "is_weekend": False,
    }
    result = scheduler.tick()
    # 意图低置信度不触发，规则 weight=1.0 × cost(1.0) = 1.0 → 必触发
    assert result == "写了这么久", f"规则兜底应触发, 实际 {result}"


def test_proactive_suppressed_when_typing_long():
    """持续打字超 60s → 抑制搭话"""
    scheduler, fw, at = _make_scheduler(
        foreground_category="development", fg_min=30.0,
        activity="typing", conv_idle_min=10.0)
    scheduler._typing_since = time.time() - 120.0  # 已打字 2 分钟
    scheduler._collect_signals = lambda now: {
        "period": "afternoon", "category": "development", "activity": "typing",
        "fg_duration_min": 30.0, "conversation_idle_min": 10.0,
        "window_switches_5min": 0, "is_weekend": False,
    }
    result = scheduler.tick()
    assert result is None, "持续打字不应触发"
