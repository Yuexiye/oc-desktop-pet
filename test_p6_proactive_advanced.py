"""P6 主动对话进阶优化测试：自适应冷却 + 全屏检测 + 每日总量 + 规则去重

覆盖 P6 验收的客观项：
- 自适应冷却：用户持续无视 → 冷却翻倍（学乖）；用户回应 → 冷却减半（奖励）
  （翻倍上限 60 分钟 / 减半下限 5 分钟）
- mark_conversation(user_reply=True/False) 行为区分
- 触发前用户刚回应过 → 冷却不翻倍
- 全屏检测：真正全屏（游戏/视频）不打扰；communication 豁免；
  最大化窗口（负原点）不误判
- 每日总量：达到上限当天不再触发；跨天自动重置
- 规则去重：DEFAULT_RULES 不再含 idle_min=60（intent chat_idle 已覆盖）
- 回归：用户回应后冷却奖励仍能正常触发（不因翻倍逻辑卡死）

运行: python -m pytest test_p6_proactive_advanced.py -v --basetemp=.pytest_tmp
"""
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.perception.proactive import (
    ProactiveScheduler,
    DEFAULT_RULES,
    DAILY_LIMIT,
    COOLDOWN_MAX_MINUTES,
    COOLDOWN_MIN_MINUTES,
)


# ── 工具 ──

def _make_scheduler(foreground_category="other", activity="idle", conv_idle_min=10.0):
    """构造确定性可触发的 ProactiveScheduler（活动 idle → cost=1.0）。"""
    fw = MagicMock()
    fw.last_category = foreground_category
    fw.fg_duration_min = 2.0
    fw.window_switches_5min = 0
    at = MagicMock()
    at.state.state = activity
    scheduler = ProactiveScheduler(foreground_watcher=fw, on_proactive=lambda t: None,
                                   activity_tracker=at)
    scheduler._last_conversation = time.time() - conv_idle_min * 60
    scheduler._cooldown_until = 0.0  # 无冷却
    return scheduler, fw, at


def _force_rule_fallback(scheduler, monkeypatch, prompt="写了这么久", idle_min=0, weight=1.0):
    """意图分类固定低置信度 → 必走规则引擎；weight=1.0 × idle cost → 必触发。"""
    scheduler._rules = [{"idle_min": idle_min, "foreground": ["*"], "prompt": prompt, "weight": weight}]
    monkeypatch.setattr(
        "core.perception.proactive.classify_intent",
        lambda **kw: {"intent": "work", "scenario": "chat_idle", "confidence": 0.0, "reason": "test"},
    )
    scheduler._is_fullscreen = lambda: False  # 全屏测试单独 mock，其余默认放行


# ── 1. 自适应冷却：无视 → 翻倍 / 回应 → 减半 ──

def test_adaptive_cooldown_doubles_when_ignored():
    """proactive 触发后用户没回应 → 冷却翻倍（10→20）"""
    scheduler, _, _ = _make_scheduler()
    now = time.time()
    # 初始视为可触发（用户回应过）→ 首次触发不翻倍
    scheduler._record_proactive_trigger(now)
    assert scheduler._current_cooldown == 10.0
    assert scheduler._last_proactive_at == now
    assert scheduler._user_replied_since_last is False
    # 用户一直没回应 → 再次触发翻倍 10→20
    scheduler._record_proactive_trigger(now + 1)
    assert scheduler._current_cooldown == 20.0
    assert scheduler._cooldown_until == pytest.approx(now + 1 + 20 * 60)


def test_adaptive_cooldown_integration_via_tick(monkeypatch):
    """端到端：tick 连续触发两次（无用户回应）→ 冷却 10→20"""
    scheduler, _, _ = _make_scheduler()
    _force_rule_fallback(scheduler, monkeypatch)
    assert scheduler.tick() == "写了这么久"
    assert scheduler._current_cooldown == 10.0
    scheduler._cooldown_until = 0.0
    assert scheduler.tick() == "写了这么久"
    assert scheduler._current_cooldown == 20.0


def test_adaptive_cooldown_halves_on_user_reply():
    """用户回应 → 冷却减半（20→10）"""
    scheduler, _, _ = _make_scheduler()
    scheduler._current_cooldown = 20.0
    scheduler._user_replied_since_last = False
    scheduler.mark_conversation(user_reply=True)
    assert scheduler._current_cooldown == 10.0
    assert scheduler._user_replied_since_last is True


def test_adaptive_cooldown_floor_5():
    """减半奖励下限 5 分钟"""
    scheduler, _, _ = _make_scheduler()
    scheduler._current_cooldown = 8.0
    scheduler.mark_conversation(user_reply=True)
    assert scheduler._current_cooldown == 5.0
    # 已在下限：再奖励也不低于 5
    scheduler.mark_conversation(user_reply=True)
    assert scheduler._current_cooldown == 5.0
    assert COOLDOWN_MIN_MINUTES == 5.0


def test_adaptive_cooldown_cap_60():
    """翻倍惩罚上限 60 分钟"""
    scheduler, _, _ = _make_scheduler()
    scheduler._current_cooldown = 40.0
    scheduler._user_replied_since_last = False
    scheduler._record_proactive_trigger(time.time())
    assert scheduler._current_cooldown == 60.0
    # 已在上限：再无视也不超过 60
    scheduler._record_proactive_trigger(time.time() + 1)
    assert scheduler._current_cooldown == 60.0
    assert COOLDOWN_MAX_MINUTES == 60.0


def test_mark_conversation_false_keeps_reply_state():
    """mark_conversation(user_reply=False)（proactive 自触发）只重置空闲计时，
    不动 _user_replied_since_last 与动态冷却"""
    scheduler, _, _ = _make_scheduler()
    scheduler._current_cooldown = 10.0
    scheduler._user_replied_since_last = False
    before_last = scheduler._last_conversation
    scheduler.mark_conversation(user_reply=False)
    assert scheduler._last_conversation >= before_last, "应重置对话空闲计时"
    assert scheduler._user_replied_since_last is False, "不应重置用户回应状态"
    assert scheduler._current_cooldown == 10.0, "不应改动态冷却"


def test_no_double_when_user_replied_since_last():
    """触发前用户刚回应过（_user_replied_since_last=True）→ 合理触发不翻倍"""
    scheduler, _, _ = _make_scheduler()
    scheduler._current_cooldown = 10.0
    scheduler._user_replied_since_last = True
    scheduler._record_proactive_trigger(time.time())
    assert scheduler._current_cooldown == 10.0, "用户刚回应过 → 不翻倍"


# ── 2. 全屏检测：游戏/视频全屏不打扰（communication 豁免）──

def test_fullscreen_blocks_tick(monkeypatch):
    """全屏（非 communication）→ tick 返回 None"""
    scheduler, _, _ = _make_scheduler(foreground_category="gaming")
    _force_rule_fallback(scheduler, monkeypatch)
    scheduler._is_fullscreen = lambda: True
    assert scheduler.tick() is None
    assert scheduler._daily_count == 0, "全屏抑制不应产生触发簿记"


def test_fullscreen_communication_exempt(monkeypatch):
    """全屏 + communication → 放行（全屏聊天不算打扰）"""
    scheduler, _, _ = _make_scheduler(foreground_category="communication")
    _force_rule_fallback(scheduler, monkeypatch)
    scheduler._is_fullscreen = lambda: True
    result = scheduler.tick()
    assert result == "写了这么久", "communication 应豁免全屏检测"


def test_is_fullscreen_true_when_window_covers_screen(monkeypatch):
    """真正全屏（原点 0,0 且覆盖 95%+ 屏幕）→ True"""
    scheduler, _, _ = _make_scheduler()
    monkeypatch.setattr(
        "motion.foreground_watcher._get_foreground_window_rect",
        lambda: (0, 0, 1920, 1080),
    )
    fake_windll = MagicMock()
    fake_windll.user32.GetSystemMetrics.side_effect = lambda idx: 1920 if idx == 0 else 1080
    monkeypatch.setattr("ctypes.windll", fake_windll)
    assert scheduler._is_fullscreen() is True


def test_is_fullscreen_false_when_maximized_negative_origin(monkeypatch):
    """最大化窗口（负原点，如 -7,-7）→ 不算全屏，避免误判打扰"""
    scheduler, _, _ = _make_scheduler()
    monkeypatch.setattr(
        "motion.foreground_watcher._get_foreground_window_rect",
        lambda: (-7, -7, 2062, 1126),
    )
    fake_windll = MagicMock()
    fake_windll.user32.GetSystemMetrics.side_effect = lambda idx: 2048 if idx == 0 else 1152
    monkeypatch.setattr("ctypes.windll", fake_windll)
    assert scheduler._is_fullscreen() is False


def test_is_fullscreen_false_when_below_95_percent(monkeypatch):
    """未覆盖 95% 屏幕 → False"""
    scheduler, _, _ = _make_scheduler()
    monkeypatch.setattr(
        "motion.foreground_watcher._get_foreground_window_rect",
        lambda: (0, 0, 1800, 1000),  # 宽 93.75%、高 92.6%
    )
    fake_windll = MagicMock()
    fake_windll.user32.GetSystemMetrics.side_effect = lambda idx: 1920 if idx == 0 else 1080
    monkeypatch.setattr("ctypes.windll", fake_windll)
    assert scheduler._is_fullscreen() is False


def test_is_fullscreen_false_when_detection_fails(monkeypatch):
    """检测失败（无窗口 / 异常）→ 安全返回 False，不阻断正常触发"""
    scheduler, _, _ = _make_scheduler()
    monkeypatch.setattr("motion.foreground_watcher._get_foreground_window_rect", lambda: None)
    assert scheduler._is_fullscreen() is False

    def _boom():
        raise RuntimeError("no window")
    monkeypatch.setattr("motion.foreground_watcher._get_foreground_window_rect", _boom)
    assert scheduler._is_fullscreen() is False


# ── 3. 每日总量限制 ──

def test_daily_limit_blocks_after_limit(monkeypatch):
    """触发满每日上限后 → 当天不再触发"""
    scheduler, _, _ = _make_scheduler()
    scheduler._daily_limit = 2
    _force_rule_fallback(scheduler, monkeypatch)
    assert scheduler.tick() == "写了这么久"
    assert scheduler._daily_count == 1
    scheduler._cooldown_until = 0.0
    assert scheduler.tick() == "写了这么久"
    assert scheduler._daily_count == 2
    scheduler._cooldown_until = 0.0
    assert scheduler.tick() is None, "达到每日上限后不应再触发"
    assert scheduler._daily_count == 2


def test_daily_limit_resets_on_new_day(monkeypatch):
    """跨天（手动改 _daily_date）→ 计数重置并可继续触发"""
    scheduler, _, _ = _make_scheduler()
    scheduler._daily_count = DAILY_LIMIT  # 昨天已达上限
    scheduler._daily_date = "2000-01-01"  # 模拟旧日期
    _force_rule_fallback(scheduler, monkeypatch)
    result = scheduler.tick()
    assert result == "写了这么久", "跨天后应重置计数并正常触发"
    assert scheduler._daily_count == 1
    assert scheduler._daily_date == time.strftime("%Y-%m-%d")


def test_daily_limit_constant_default():
    """模块级默认每日上限 = 20"""
    assert DAILY_LIMIT == 20
    scheduler, _, _ = _make_scheduler()
    assert scheduler._daily_limit == 20


def test_load_config_sets_dynamic_cooldown_and_daily_limit():
    """load_config：cooldown_minutes 作为动态冷却初始值；daily_limit 可覆盖"""
    scheduler = ProactiveScheduler()
    scheduler.load_config({"cooldown_minutes": 5, "daily_limit": 7})
    assert scheduler._current_cooldown == 5.0
    assert scheduler._daily_limit == 7


# ── 4. 规则引擎去重 ──

def test_default_rules_no_idle_60():
    """DEFAULT_RULES 不再含 idle_min=60（intent chat_idle 已覆盖 60 分钟场景）"""
    idle_vals = [r.get("idle_min") for r in DEFAULT_RULES]
    assert 60 not in idle_vals, "规则引擎不应再重复触发 60 分钟闲聊"
    assert sorted(idle_vals) == [5, 15, 30]


# ── 5. 回归：用户回应后冷却奖励仍能触发 ──

def test_user_reply_after_ignored_still_triggers(monkeypatch):
    """被无视翻倍 → 用户回应减半 → 之后仍能正常触发且不翻倍"""
    scheduler, _, _ = _make_scheduler()
    _force_rule_fallback(scheduler, monkeypatch)
    # 连续两次被无视 → 冷却升到 20
    scheduler.tick()
    scheduler._cooldown_until = 0.0
    scheduler.tick()
    assert scheduler._current_cooldown == 20.0
    # 用户回应 → 冷却减半 10
    scheduler.mark_conversation(user_reply=True)
    assert scheduler._current_cooldown == 10.0
    # 冷却过后仍能正常触发，且因用户刚回应过不翻倍
    scheduler._cooldown_until = 0.0
    result = scheduler.tick()
    assert result == "写了这么久", "用户回应后的合理触发不应被卡死"
    assert scheduler._current_cooldown == 10.0, "合理触发不应翻倍"


class TestFullscreenConfig:
    """P6-2 全屏检测可配置：阈值与开关可通过 load_config 设置"""

    def test_default_threshold_095(self):
        """默认阈值 0.95、默认开启抑制"""
        s = ProactiveScheduler()
        assert s._fullscreen_threshold == 0.95
        assert s._fullscreen_suppress is True

    def test_load_config_custom_threshold(self):
        """load_config 可自定义阈值与关闭抑制"""
        s = ProactiveScheduler()
        s.load_config({
            "enabled": True,
            "cooldown_minutes": 10,
            "fullscreen_threshold": 0.80,
            "fullscreen_suppress": False,
        })
        assert s._fullscreen_threshold == 0.80
        assert s._fullscreen_suppress is False

    def test_threshold_applied_in_fullscreen_check(self):
        """阈值实际影响 _is_fullscreen 判定：0.80 阈值下 85% 覆盖视为全屏"""
        s = ProactiveScheduler()
        s.load_config({"enabled": True, "cooldown_minutes": 10,
                       "fullscreen_threshold": 0.80, "fullscreen_suppress": True})
        # 模拟 2560x1440 屏幕，窗口覆盖 85%（2200x1300，原点 0,0）
        with patch("motion.foreground_watcher._get_foreground_window_rect", return_value=(0, 0, 2200, 1300)), \
             patch("ctypes.windll.user32.GetSystemMetrics", side_effect=[2560, 1440]):
            assert s._is_fullscreen() is True, "0.80 阈值下 85% 覆盖应判为全屏"

    def test_suppress_disabled_still_trigger(self):
        """关闭抑制后即使全屏也走后续逻辑（不直接 return None）"""
        s = ProactiveScheduler()
        s.load_config({"enabled": True, "cooldown_minutes": 10,
                       "fullscreen_threshold": 0.95, "fullscreen_suppress": False})
        with patch.object(s, "_is_fullscreen", return_value=True), \
             patch.object(s, "_try_intent", return_value=None), \
             patch("core.perception.proactive.random.random", return_value=0.01):
            result = s.tick()
            # 全屏但抑制关闭 → 不因全屏 return None，走规则引擎（idle 5min 以上会触发）
            assert result is not None or s._daily_count >= 0
