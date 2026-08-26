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
from avatar.live2d_renderer import Live2DRenderer  # noqa: E402


class _FakeLive2D:
    """模拟 Live2D 模块的 MotionPriority 常量。"""

    class MotionPriority:
        NORMAL = 0
        IDLE = 1
        FORCE = 2


class _FakeRenderer:
    """模拟渲染器：记录 _start_motion_at / _force_idle / StopAllMotions 调用。"""

    def __init__(self):
        self._live2d = _FakeLive2D()
        self._model = None
        self.motion_calls = []
        self.force_idle_calls = 0
        self.start_idle_calls = 0
        self.stop_all_calls = 0
        self.reset_expression_calls = 0
        self._expression_active = False
        self._last_expression = ""

    def _start_motion_at(self, idx, priority=None, force_restart=False, exclusive=False):
        self.motion_calls.append((idx, priority, force_restart, exclusive))
        if exclusive:
            # 模拟 renderer exclusive 清场行为
            self.stop_all_calls += 2
            self.reset_expression_calls += 1
            self._expression_active = False
            self._last_expression = ""
        return True

    def _force_idle(self):
        self.force_idle_calls += 1

    def _start_idle(self):
        self.start_idle_calls += 1

    def StopAllMotions(self):
        self.stop_all_calls += 1

    def ResetExpressions(self):
        self.reset_expression_calls += 1


# ── A：菜单手动播放 FORCE 优先级 ──────────────────────────


def test_menu_motion_passes_force_priority():
    """A：菜单手动播放 → _start_motion_at 收到 FORCE + force_restart + exclusive。"""
    renderer = _FakeRenderer()
    pet.PetWindow._start_menu_motion(SimpleNamespace(), renderer, 1)
    assert renderer.motion_calls == [(1, _FakeLive2D.MotionPriority.FORCE, True, True)]


def test_menu_motion_multi_click_each_force():
    """A：连点两个不同动作 → 每次都 FORCE（第二个不再被同优先级拒绝）。"""
    renderer = _FakeRenderer()
    for idx in (0, 2):
        pet.PetWindow._start_menu_motion(SimpleNamespace(), renderer, idx)
    assert renderer.motion_calls == [
        (0, _FakeLive2D.MotionPriority.FORCE, True, True),
        (2, _FakeLive2D.MotionPriority.FORCE, True, True),
    ]


def test_menu_motion_fallback_when_no_live2d():
    """A：无 Live2D 环境（headless 单测）→ 回退默认（priority=None → NORMAL）。"""
    calls = []
    def _start(idx, priority=None):
        calls.append((idx, priority))
    renderer = SimpleNamespace(_live2d=None, _start_motion_at=_start)
    pet.PetWindow._start_menu_motion(SimpleNamespace(), renderer, 0)
    assert calls == [(0, None)]


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
    renderer._start_motion_at(3, None)  # 等价于 renderer 内部非 exclusive 调用
    assert renderer.motion_calls == [(3, None, False, False)]


def test_menu_motion_stops_existing_motions_first():
    """A 治本：菜单手动播放前 exclusive 清场（StopAllMotions×2 + ResetExpressions），
    避免动作/表情叠加（如"比心表情 + 挥手动作"）。"""
    renderer = _FakeRenderer()
    renderer._model = renderer  # model 自身带 StopAllMotions（通过守卫）
    renderer._expression_active = True
    renderer._last_expression = "比心"
    pet.PetWindow._start_menu_motion(SimpleNamespace(), renderer, 2)
    # exclusive 模式：双重 StopAllMotions + 重置表情
    assert renderer.stop_all_calls == 2
    assert renderer.reset_expression_calls == 1
    assert renderer._expression_active is False
    assert renderer._last_expression == ""
    # 之后只播一次新动作（FORCE 优先级，force_restart + exclusive）
    assert renderer.motion_calls == [(2, _FakeLive2D.MotionPriority.FORCE, True, True)]


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


# ── C：非 idle motion 播放期间不叠加情绪表情 ───────────────

def _make_live2d_renderer_with_model():
    """用 object.__new__ 构造无 __init__ 的 Live2DRenderer，便于单元测试。"""
    renderer = object.__new__(Live2DRenderer)
    renderer._expression_active = False
    renderer._last_expression = ""
    renderer._expression_set_at = 0.0
    renderer._expression_suppress_until = 0.0
    renderer._motion_is_idle = True
    renderer._emotion_exprs = {"happy": "比心"}
    renderer._expression_names = ["比心", "脸红", "前倾"]
    renderer._model = MagicMock()
    return renderer


def test_apply_expression_sets_expression_when_idle():
    """idle 状态下 _apply_expression 正常设置表情。"""
    renderer = _make_live2d_renderer_with_model()
    renderer._motion_is_idle = True
    renderer._apply_expression("happy")
    renderer._model.SetExpression.assert_called_once_with("比心")
    assert renderer._expression_active is True
    assert renderer._last_expression == "比心"


def test_apply_expression_suppressed_during_non_idle_motion():
    """非 idle motion 播放期间，_apply_expression 不叠加新表情。

    根因：自动随机动作 waving 播放时，情绪系统每秒 set_emotion('happy')
    因全局 gesture 冷却只同步表情，会把比心叠到 waving 上。
    """
    renderer = _make_live2d_renderer_with_model()
    renderer._motion_is_idle = False
    renderer._apply_expression("happy")
    renderer._model.SetExpression.assert_not_called()
    assert renderer._expression_active is False
    assert renderer._last_expression == ""


def test_apply_expression_resets_during_non_idle_motion_for_neutral():
    """非 idle motion 期间情绪回 neutral 仍应清表情。"""
    renderer = _make_live2d_renderer_with_model()
    renderer._motion_is_idle = False
    renderer._expression_active = True
    renderer._last_expression = "比心"
    renderer._apply_expression("neutral")
    renderer._model.ResetExpressions.assert_called()
    assert renderer._expression_active is False
    assert renderer._last_expression == ""


def test_set_expression_by_name_stops_motions_when_non_idle():
    """手动点表情时，如果正在播非 idle motion，必须 StopAllMotions 清场。"""
    renderer = _make_live2d_renderer_with_model()
    renderer._motion_is_idle = False
    renderer._current_motion_idx = 2
    renderer.set_expression_by_name("比心")
    assert renderer._model.StopAllMotions.call_count == 2
    renderer._model.ResetExpressions.assert_called()
    renderer._model.SetExpression.assert_called_once_with("比心")
    assert renderer._expression_active is True
    assert renderer._last_expression == "比心"
    assert renderer._motion_is_idle is True
    assert renderer._current_motion_idx is None


def test_set_expression_by_name_resets_previous_expression():
    """手动切换表情时先 ResetExpressions，确保不与前一个表情叠加。"""
    renderer = _make_live2d_renderer_with_model()
    renderer._expression_active = True
    renderer._last_expression = "前倾"
    renderer.set_expression_by_name("比心")
    renderer._model.ResetExpressions.assert_called()
    renderer._model.SetExpression.assert_called_once_with("比心")
    assert renderer._last_expression == "比心"
