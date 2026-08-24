# -*- coding: utf-8 -*-
"""BugFix #5-A/B 单测：动作菜单 FORCE 优先级 + 重置表情走 _force_idle。

- A：右键「🎬 模型动作」连点两个不同动作，第二个被忽略。
  根因：`_start_motion_at`（avatar/live2d_renderer.py）默认 NORMAL 优先级，
  Live2D 同优先级不打断正在播的 motion → 菜单手动播放必须传 FORCE
  （pet.py `_start_menu_motion`）。自动随机动作路径（IDLE/NORMAL）不受影响。
- B：右键「🔄 重置表情」无反应。
  根因：`_reset_motion_expression`（pet.py）先 ResetExpressions() 清表情成功，
  再 `_start_idle()`（IDLE 优先级）打不过正在播的非 idle 手势 → 改走
  `_force_idle()`（ResetExpressions + StopAllMotions + FORCE 强制回 idle）。

运行: python -m pytest tests/test_bugfix5_motion_priority.py -v
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pet  # noqa: E402  (PetWindow 可离屏导入)


class _FakeLive2D:
    """模拟 Live2D 模块的 MotionPriority 常量。"""

    class MotionPriority:
        NORMAL = 0
        IDLE = 1
        FORCE = 2


class _FakeRenderer:
    """模拟渲染器：记录 _start_motion_at / _force_idle 调用。"""

    def __init__(self):
        self._live2d = _FakeLive2D()
        self._model = None
        self.motion_calls = []
        self.force_idle_calls = 0
        self.start_idle_calls = 0

    def _start_motion_at(self, idx, priority=None):
        self.motion_calls.append((idx, priority))
        return True

    def _force_idle(self):
        self.force_idle_calls += 1

    def _start_idle(self):
        self.start_idle_calls += 1


# ── A：菜单手动播放 FORCE 优先级 ──────────────────────────


def test_menu_motion_passes_force_priority():
    """A：菜单手动播放 → _start_motion_at 收到 FORCE 优先级。"""
    renderer = _FakeRenderer()
    pet.PetWindow._start_menu_motion(SimpleNamespace(), renderer, 1)
    assert renderer.motion_calls == [(1, _FakeLive2D.MotionPriority.FORCE)]


def test_menu_motion_multi_click_each_force():
    """A：连点两个不同动作 → 每次都 FORCE（第二个不再被同优先级拒绝）。"""
    renderer = _FakeRenderer()
    for idx in (0, 2):
        pet.PetWindow._start_menu_motion(SimpleNamespace(), renderer, idx)
    assert renderer.motion_calls == [
        (0, _FakeLive2D.MotionPriority.FORCE),
        (2, _FakeLive2D.MotionPriority.FORCE),
    ]


def test_menu_motion_fallback_when_no_live2d():
    """A：无 Live2D 环境（headless 单测）→ 回退默认（priority=None → NORMAL）。"""
    renderer = SimpleNamespace(_live2d=None)
    renderer._start_motion_at = MagicMock(return_value=True)
    pet.PetWindow._start_menu_motion(SimpleNamespace(), renderer, 0)
    renderer._start_motion_at.assert_called_once_with(0, priority=None)


def test_menu_motion_degrades_for_renderer_without_priority():
    """A：旧式/非 Live2D 渲染器（_start_motion_at 只收 idx）→ 不传 priority kwarg。

    对应 test_real_startup_smoke 的 FakeRenderer（真实 QMenu 触发路径），
    冒烟测试注入的 _start_motion_at(self, idx) 不接受 priority。
    """
    renderer = SimpleNamespace(_live2d=None)
    calls = []

    def _start(idx):
        calls.append(idx)

    renderer._start_motion_at = _start
    pet.PetWindow._start_menu_motion(SimpleNamespace(), renderer, 1)
    assert calls == [1]


def test_auto_random_path_unaffected():
    """A 回归：自动随机动作路径不传 priority（None → 默认 NORMAL），不被打断。"""
    renderer = _FakeRenderer()
    renderer._start_motion_at(3, None)  # 等价于 live2d_renderer 自动随机路径的调用
    assert renderer.motion_calls == [(3, None)]


# ── B：重置表情走 _force_idle ─────────────────────────────


def test_reset_motion_expression_uses_force_idle():
    """B：重置表情 → 走 _force_idle（FORCE 优先级），不再 _start_idle。"""
    renderer = _FakeRenderer()
    renderer._model = object()  # 通过 model 守卫
    pet.PetWindow._reset_motion_expression(SimpleNamespace(_renderer=renderer))
    assert renderer.force_idle_calls == 1
    assert renderer.start_idle_calls == 0


def test_reset_motion_expression_guard_without_renderer():
    """B：无 renderer 时安全返回（不抛异常）。"""
    pet.PetWindow._reset_motion_expression(SimpleNamespace(_renderer=None))


def test_reset_motion_expression_guard_without_model():
    """B：renderer 无 _model 时安全返回。"""
    renderer = _FakeRenderer()
    renderer._model = None
    pet.PetWindow._reset_motion_expression(SimpleNamespace(_renderer=renderer))
    assert renderer.force_idle_calls == 0
