# -*- coding: utf-8 -*-
"""真实启动冒烟测试 — 覆盖单元测试盲区。

背景（2026-08-20 用户真机反馈）：启动后两个问题——
1. _unified_tick → _focus_manager() TypeError（行为层方法与注入属性同名）
2. 右键菜单动作/表情项 lambda 缺 checked 参数崩溃（PySide6 addAction slot
   不传 checked），用户点"切换表情"无效果。

这两个都是"真实对象 + 真实 PySide6 绑定行为"才暴露的，纯 mock 单测覆盖不到。
本测试用离屏 QApplication + 真实 PetWindow（完整 T05/P1/P2 接线）+ 真实
QMenu/QAction triggered 信号，模拟真实启动与交互路径，防止回归。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("OC_DISABLE_TRAY", "1")
os.environ.setdefault("OC_DISABLE_PERCEPTION", "1")
os.environ.setdefault("OC_DISABLE_LIVE2D", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


class FakeRenderer:
    """离屏无 GL：用真实接口形状的 renderer 替代 Live2DRenderer。

    _motion_files / _expression_names 非空以让菜单真实创建项目，
    触发真实 QAction.triggered → 真实 lambda → 验证 addAction slot 调用方式。
    """

    def __init__(self):
        self._motion_files = [
            "motions/idle.motion3.json",
            "motions/happy.motion3.json",
        ]
        self._expression_names = ["比心", "唱歌"]
        self.called_motion: int | None = None
        self.called_expr: str | None = None

    def _start_motion_at(self, idx: int) -> None:
        self.called_motion = idx

    def set_expression_by_name(self, name: str) -> None:
        self.called_expr = name


@pytest.fixture()
def window(app):
    import types

    from pet import PetWindow

    w = PetWindow(agent_id="miku")
    # OC_DISABLE_LIVE2D=1 时真实 renderer 是 SpriteRenderer：保留真实属性方法
    #（tick 会访问 _base_label_pos/_gaze_offset_x 等），只注入菜单所需数据。
    r = w._renderer
    r._motion_files = ["motions/idle.motion3.json", "motions/happy.motion3.json"]
    r._expression_names = ["比心", "唱歌"]
    r.called_motion: int | None = None
    r.called_expr: str | None = None

    if not hasattr(r, "_start_motion_at"):
        def _start_motion_at(self, idx: int) -> None:
            self.called_motion = idx

        r._start_motion_at = types.MethodType(_start_motion_at, r)
    if not hasattr(r, "set_expression_by_name"):
        def _set_expr(self, name: str) -> None:
            self.called_expr = name

        r.set_expression_by_name = types.MethodType(_set_expr, r)

    # 建真实 QMenu 子菜单（右键菜单里 addMenu 的实例；这里手动建）
    w._motion_submenu = QMenu("模型动作")
    yield w
    try:
        w.close()
    except Exception:
        pass


class TestRealStartupSmoke:
    def test_window_constructed_with_all_neko_wiring(self, window):
        """真实 PetWindow 构造后四线接线对象应就位（防御式任一失败不崩）。"""
        # T05 focus
        fm = getattr(window, "_focus_manager", None)
        assert fm is not None, "T05 focus core 未注入"
        # T05 面板（真实属性名带下划线）
        assert getattr(window, "_chat_panel", None) is not None, "chat panel 缺失"
        assert getattr(window, "_memory_panel", None) is not None, "memory panel 缺失"
        # T02 proactive（anti_repeat 注入在其内部，无 self 属性）
        assert getattr(window, "_proactive", None) is not None, "proactive 缺失"
        # P1 FactStore / Reflection
        assert getattr(window, "_fact_store", None) is not None, "FactStore 未注入"
        assert getattr(window, "_reflection_engine", None) is not None, "ReflectionEngine 未注入"

    def test_unified_tick_no_crash_with_real_focus_injection(self, window):
        """真实 _focus_manager 实例注入下，多轮 tick 不崩（2026-08-20 崩溃回归）。"""
        for _ in range(5):
            window._unified_tick()  # 覆盖 _can_idle_chatter → _focus_suppresses_proactive

    def test_rebuild_motion_menu_and_trigger_no_crash(self, window):
        """真实 QMenu addAction + QAction.trigger() → lambda 无 checked 崩溃（回归）。

        PySide6 的 QMenu.addAction(text, callable) 在本环境不传 checked，
        旧代码 lambda checked 必崩；*args 修复后应正常。
        """
        window._rebuild_motion_menu()
        actions = window._motion_submenu.actions()
        # 应有：动作标题 + 2 motion + separator + 表情标题 + 2 expr + separator + 重置
        motion_items = [a for a in actions if "▶️" in a.text()]
        expr_items = [a for a in actions if "✨" in a.text()]
        assert len(motion_items) == 2, f"动作项 {len(motion_items)} != 2"
        assert len(expr_items) == 2, f"表情项 {len(expr_items)} != 2"

        # 触发动作项（真实 triggered 信号，会传 checked）
        motion_items[0].trigger()
        assert window._renderer.called_motion == 0
        # 触发表情项
        expr_items[0].trigger()
        assert window._renderer.called_expr == "比心"

    def test_reset_expression_menu_item(self, window):
        """重置表情菜单项（直接绑定方法，不受 lambda 影响）应存在且可触发。"""
        window._rebuild_motion_menu()
        reset_items = [a for a in window._motion_submenu.actions() if "重置表情" in a.text()]
        assert len(reset_items) == 1
        reset_items[0].trigger()  # 调用 _reset_motion_expression，renderer 为假对象需兜底
