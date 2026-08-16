"""P4-1 表情超时重置测试：验证"比心/葱"等贴图开关表情不再永久挂起。

背景：miku 模型用 Param131-137 贴图开关做"比心/葱/唱歌/前倾"等手势表情。
旧逻辑：happy 情绪每帧 set_emotion → SetExpression("比心") → 表情永不重置，
用户反复反馈的"一直比心"就是它（motion 有 GESTURE_TIMEOUT 兜底，expression 没有）。

修复：_apply_expression 同表情激活中不刷新超时；_expire_expression_if_stale
超时自动 ResetExpressions + 冷却期防闪烁。
"""
import time
from unittest.mock import MagicMock

import pytest

from avatar.live2d_renderer import Live2DRenderer


def _make_renderer(expressions=("比心", "葱", "唱歌", "前倾", "QQ人", "圈圈", "脸红")):
    """构造不带 Qt 的 renderer 实例（live2d 模型用 MagicMock 替代）。"""
    r = Live2DRenderer.__new__(Live2DRenderer)
    r._model = MagicMock()
    r._live2d = MagicMock()
    # live2d-py StandardParams 引用（程序化层用）
    P = MagicMock()
    for n in ("ParamEyeLSmile", "ParamEyeRSmile", "ParamBrowLAngle", "ParamBrowRAngle",
              "ParamBrowLForm", "ParamBrowRForm", "ParamMouthForm", "ParamHairFront",
              "ParamHairSide", "ParamBodyAngleX", "ParamBodyAngleY"):
        setattr(P, n, n)
    r._live2d.StandardParams = P
    r._expression_names = list(expressions)
    r._expression_set_at = 0.0
    r._expression_active = False
    r._last_expression = ""
    r._expression_suppress_until = 0.0
    r._motion_is_idle = True
    r._motion_started_at = time.monotonic()
    r._last_gesture_at = 0.0
    r._current_emotion = "neutral"
    r._emotion_target = "neutral"
    r._emotion_motion_cooldown = {}
    r._proc_cur = {"smile": 0.0, "brow": 0.0, "mouth": 0.0}
    r._current_motion_idx = None
    r._debug_minimal = True
    return r


class TestExpressionTimeout:
    def test_happy_matches_bixin_expression(self):
        """happy 情绪应命中"比心"表情（贴图开关）"""
        r = _make_renderer()
        expr = r._match_expression("happy")
        assert expr == "比心", f"happy 应匹配比心，实际 {expr}"

    def test_neutral_resets_expression(self):
        """neutral 情绪应 ResetExpressions"""
        r = _make_renderer()
        r._model.ResetExpressions = MagicMock()
        Live2DRenderer._apply_expression(r, "happy")
        assert r._expression_active is True
        assert r._last_expression == "比心"
        r._model.ResetExpressions.reset_mock()
        Live2DRenderer._apply_expression(r, "neutral")
        r._model.ResetExpressions.assert_called_once()

    def test_same_expression_does_not_refresh_timeout(self):
        """同表情重复设置不应刷新超时时间戳（否则表情永远不过期）"""
        r = _make_renderer()
        Live2DRenderer._apply_expression(r, "happy")
        first_set_at = r._expression_set_at
        # 模拟持续 happy（每帧 set_emotion 都会走 _apply_expression）
        time.sleep(0.02)
        Live2DRenderer._apply_expression(r, "happy")
        assert r._expression_set_at == first_set_at, "同表情不应刷新超时时间戳"

    def test_expression_expires_after_timeout(self):
        """表情播满 GESTURE_TIMEOUT 后应自动重置回默认"""
        r = _make_renderer()
        r._model.ResetExpressions = MagicMock()
        Live2DRenderer._apply_expression(r, "happy")
        assert r._expression_active is True
        # 伪造已过超时
        r._expression_set_at = time.monotonic() - r.GESTURE_TIMEOUT - 0.1
        Live2DRenderer._expire_expression_if_stale(r)
        r._model.ResetExpressions.assert_called_once()
        assert r._expression_active is False, "超时后表情应重置"

    def test_no_reset_before_timeout(self):
        """超时前不应重置表情"""
        r = _make_renderer()
        r._model.ResetExpressions = MagicMock()
        Live2DRenderer._apply_expression(r, "happy")
        Live2DRenderer._expire_expression_if_stale(r)
        r._model.ResetExpressions.assert_not_called()
        assert r._expression_active is True

    def test_suppress_window_prevents_flicker(self):
        """重置后的冷却期内不重播同表情（防情绪持续时 3 秒亮/灭闪烁）"""
        r = _make_renderer()
        r._model.ResetExpressions = MagicMock()
        r._model.SetExpression = MagicMock()
        # 播放 happy → 超时重置
        Live2DRenderer._apply_expression(r, "happy")
        r._expression_set_at = time.monotonic() - r.GESTURE_TIMEOUT - 0.1
        Live2DRenderer._expire_expression_if_stale(r)
        # 冷却期内再次 happy → 不应 SetExpression
        r._model.SetExpression.reset_mock()
        Live2DRenderer._apply_expression(r, "happy")
        r._model.SetExpression.assert_not_called()
        assert r._expression_active is False

    def test_new_expression_after_cooldown_plays(self):
        """冷却期结束后可重播表情（但由超时控制）"""
        r = _make_renderer()
        r._model.ResetExpressions = MagicMock()
        r._model.SetExpression = MagicMock()
        # 播放 happy → 超时重置 → 冷却结束
        Live2DRenderer._apply_expression(r, "happy")
        r._expression_set_at = time.monotonic() - r.GESTURE_TIMEOUT - 0.1
        Live2DRenderer._expire_expression_if_stale(r)
        r._expression_suppress_until = time.monotonic() - 0.1  # 冷却结束
        r._model.SetExpression.reset_mock()
        Live2DRenderer._apply_expression(r, "happy")
        r._model.SetExpression.assert_called_once_with("比心")

    def test_different_emotion_switches_expression(self):
        """情绪变化应切换表情（happy→比心，cute→脸红）"""
        r = _make_renderer()
        r._model.SetExpression = MagicMock()
        Live2DRenderer._apply_expression(r, "happy")
        assert r._last_expression == "比心"
        r._model.SetExpression.reset_mock()
        Live2DRenderer._apply_expression(r, "cute")
        r._model.SetExpression.assert_called_once_with("脸红")
