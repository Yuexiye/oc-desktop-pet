"""Live2D 修复回归测试（无头，mock 模型）

覆盖 2026-08-15 晚的两个修复：
  F1 头顶被截 + 比例瘦高：
     - _fit_offset_y 上限 0.28（旧 0.45 会把头顶推出窗口）
     - target_h 受 canvas 高度约束（至少装下模型高度 82%，避免瘦高）
     - pad_bottom 降到 60（旧 200 造成窗口过高）
  F2 比心/手势持久卡死：
     - 全局 gesture 冷却：非 idle motion 播放后 GESTURE_TIMEOUT 内不再播新 motion
     - 回 idle 后冷却仍生效（防"比心→idle→立刻又比心"）
     - 冷却期内只同步表情（SetExpression/ResetExpressions 被调，StartMotion 不被调）

运行: python -m pytest test_live2d_fix.py -v
"""
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from avatar.live2d_renderer import Live2DRenderer


@pytest.fixture
def renderer():
    """构造 Live2DRenderer，绕过 __init__，注入 fake model"""
    obj = object.__new__(Live2DRenderer)
    obj._model = MagicMock()
    obj._model.GetCanvasSizePixel.return_value = (1200.0, 2134.0)  # 瘦高画布，模拟 miku
    obj._model.GetMotions.return_value = {"": [
        {"File": "idle_motion.motion3.json"},
        {"File": "happy_bixin.motion3.json"},
    ]}
    obj._live2d = MagicMock()
    obj._live2d.MotionPriority.IDLE = 0
    obj._live2d.MotionPriority.NORMAL = 1
    obj._live2d.MotionPriority.FORCE = 2
    obj._gl_w = 458
    obj._gl_h = 520
    obj._fit_scale = 1.0
    obj._fit_scale_x = 1.0
    obj._fit_offset_y = 0.0
    obj._center_offset_x = 0.0
    obj._offset_scale = (0.0, 0.0)
    obj._ready = True
    obj._debug_minimal = False
    obj._debug = False
    obj._motion_files = ["idle_motion.motion3.json", "happy_bixin.motion3.json"]
    obj._motion_group_name = ""
    obj._motion_groups = {"": []}
    obj._motion_is_idle = True
    obj._motion_started_at = time.monotonic()
    obj._current_motion_idx = None
    obj._emotion_motion_cooldown = {}
    obj._last_gesture_at = 0.0
    obj._expression_names = []
    obj._model.IsMotionFinished.return_value = True
    obj._suppress_watermark = MagicMock()
    obj._apply_expression = MagicMock()
    return obj


# ── F1: fit 修复 ──

def test_fit_offset_y_upper_bound(renderer):
    """_fit_offset_y 上限 0.28，避免把头顶推出窗口"""
    # 模拟 _fit_window_to_model 扫描出小 bbox（不含头发/脚）
    renderer._scan_bbox_adaptive = MagicMock(return_value=(100, 100, 300, 400))
    renderer._parent = MagicMock()
    renderer._parent.fit_window_to_model = MagicMock()
    Live2DRenderer._fit_window_to_model(renderer)
    assert renderer._fit_offset_y <= 0.28, f"_fit_offset_y={renderer._fit_offset_y} 超过 0.28"
    assert renderer._fit_offset_y >= 0.10, f"_fit_offset_y={renderer._fit_offset_y} 低于 0.10 保底"


def test_fit_window_height_enough_for_canvas(renderer):
    """撤除 canvas 约束后：窗口只由 bbox + 边距决定，不会被超高 canvas 撑大

    旧实现（已撤除）用 ch_px * fit_scale * 0.82 当 canvas 约束，会把窗口撑到
    数千像素（miku canvas 8888px → 7288px）。新策略：只按 bbox + 合理边距
    算窗口，确保 fit 后的窗口贴合到模型实际可见区域。
    """
    renderer._model.GetCanvasSizePixel.return_value = (1200.0, 2134.0)
    renderer._scan_bbox_adaptive = MagicMock(return_value=(100, 100, 300, 400))
    renderer._parent = MagicMock()
    renderer._parent.fit_window_to_model = MagicMock()
    Live2DRenderer._fit_window_to_model(renderer)
    target_h = renderer._parent.fit_window_to_model.call_args[0][1]
    # bbox 300x400 → 窗口高 ≈ bbox + pad_h(72) + pad_bottom(60) = 532
    assert target_h < 700, f"窗口高度 {target_h} 超出合理范围（应 < 700，避免 canvas 撑大）"
    # 也不应过小导致裁头顶：至少 ≥ bbox 高 + 一些边距
    assert target_h >= 400, f"窗口高度 {target_h} 太小，模型会被裁"


def test_fit_pad_bottom_reduced(renderer):
    """pad_bottom 足够装下脚部估算（旧 200 太大/60 太小，目前 0.25*bh+80 防截脚）"""
    renderer._scan_bbox_adaptive = MagicMock(return_value=(100, 100, 300, 400))
    renderer._parent = MagicMock()
    renderer._parent.fit_window_to_model = MagicMock()
    Live2DRenderer._fit_window_to_model(renderer)
    # bbox 高 300 → pad_bottom=80, target_h ≈ 300+pad_h+80 < 600
    target_w = renderer._parent.fit_window_to_model.call_args[0][0]
    target_h = renderer._parent.fit_window_to_model.call_args[0][1]
    assert target_h < 700, f"窗口高度 {target_h} 超出（含脚部后还是过大）"
    # 应至少能装下 bbox + 脚部估算（80px）
    assert target_h >= 400, f"窗口高度 {target_h} 太小，脚部截断"
    assert target_w / target_h > 0.25, f"宽高比 {target_w}/{target_h} 过瘦"


# ── F2: 手势冷却修复 ──

def test_global_gesture_cooldown_blocks_repeat(renderer):
    """比心播放后 3 秒内，再推 happy 只同步表情，不重播 motion"""
    # 模拟比心正在播（非 idle）
    renderer._motion_is_idle = False
    renderer._motion_started_at = time.monotonic() - 1.0  # 已播 1s（< 3s 冷却期）
    renderer._model.StartMotion = MagicMock()
    # 推 happy
    Live2DRenderer.set_emotion(renderer, "happy")
    # 冷却期内：不应调用 StartMotion
    renderer._model.StartMotion.assert_not_called()
    # 但表情应该被同步（_apply_expression 被调）
    renderer._apply_expression.assert_called_once_with("happy")


def test_global_gesture_cooldown_after_idle(renderer):
    """回到 idle 后 3 秒内仍不重播（防 比心→idle→立刻又比心）"""
    # 模拟刚回到 idle，但距上次 gesture 不到 3s
    renderer._motion_is_idle = True
    renderer._last_gesture_at = time.monotonic() - 1.0  # 1s 前播过手势
    renderer._model.StartMotion = MagicMock()
    Live2DRenderer.set_emotion(renderer, "happy")
    renderer._model.StartMotion.assert_not_called()


def test_gesture_plays_after_cooldown(renderer):
    """冷却期过后，新情绪正常播放 motion"""
    renderer._motion_is_idle = True
    renderer._last_gesture_at = time.monotonic() - 10.0  # 10s 前（> 3s 冷却）
    renderer._emotion_motion_cooldown = {}
    renderer._model.StartMotion = MagicMock()
    renderer._model.StartRandomMotion = MagicMock()
    # _play_motion_kw 依赖 _motion_files 匹配 happy_bixin
    Live2DRenderer.set_emotion(renderer, "happy")
    # 冷却期过：应播放 motion（StartMotion 或 StartRandomMotion 至少一个被调）
    assert (
        renderer._model.StartMotion.called
        or renderer._model.StartRandomMotion.called
    ), "冷却期后应播放手势"


def test_same_motion_dedup_no_timer_reset(renderer):
    """同一 motion 已在播：不重置计时（去重逻辑）"""
    renderer._motion_is_idle = False
    renderer._motion_started_at = time.monotonic() - 2.9  # 接近超时
    renderer._current_motion_idx = 1  # happy_bixin
    # _start_motion_at 遇到同一 idx 应直接返回 True 且不重置 _motion_started_at
    t_before = renderer._motion_started_at
    result = Live2DRenderer._start_motion_at(renderer, 1)
    assert result is True
    assert renderer._motion_started_at == t_before, "同一 motion 去重时不应重置计时"


def test_draw_timeout_forces_idle(renderer):
    """非 idle motion 播满 GESTURE_TIMEOUT，draw 中强制回 idle"""
    renderer._motion_is_idle = False
    renderer._motion_started_at = time.monotonic() - 4.0  # 超过 3s 超时
    renderer._model.IsMotionFinished.return_value = False
    renderer._force_idle = MagicMock()
    # 走 draw 的超时检查路径
    renderer._frame_update = MagicMock()
    renderer._update_gaze_params = MagicMock()
    renderer._update_mouth = MagicMock()
    renderer._model.Draw = MagicMock()
    renderer._live2d.clearBuffer = MagicMock()
    Live2DRenderer.draw(renderer)
    renderer._force_idle.assert_called_once()
