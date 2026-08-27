"""手动演示锁定（动作展示互斥）回归测试。

右键菜单「动作展示」选动作/表情后进入 30s 演示锁定：
- 程序化表情层（每帧按情绪写眼/眉/嘴/脸红）跳过，避免把手动选择"冲淡"；
- 呼吸/眼神/头发等自然律动照常写，不让角色"僵住"；
- 自动情绪系统（set_emotion / _tick_auto_motion / 表情过期）在锁定期间让位；
- 「重置表情」(_force_idle) 或超时后自动恢复。

重点回归：_update_procedural_emotion 顶部必须定义 `_override = self._in_manual_override()`。
若漏定义，方法内 `if not _override:` 会抛 NameError，被方法级 `except Exception: pass`
静默吞掉，导致整个程序化表情层失效（眼/眉/嘴/脸红全不写）——本文件 test_override_*
用例专门卡住这条路径。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication  # noqa: F401  (offscreen QPA 需要 app)

from avatar.live2d_renderer import Live2DRenderer

_app = QApplication.instance() or QApplication([])


class _FakeParams:
    ParamEyeLOpen = "EyeLOpen"
    ParamEyeROpen = "EyeROpen"
    ParamEyeLSmile = "EyeLSmile"
    ParamEyeRSmile = "EyeRSmile"
    ParamBrowLAngle = "BrowLAngle"
    ParamBrowRAngle = "BrowRAngle"
    ParamBrowLForm = "BrowLForm"
    ParamBrowRForm = "BrowRForm"
    ParamMouthForm = "MouthForm"
    ParamMouthOpenY = "MouthOpenY"
    ParamEyeBallX = "EyeBallX"
    ParamEyeBallY = "EyeBallY"
    ParamAngleX = "AngleX"
    ParamAngleY = "AngleY"
    ParamBreath = "Breath"
    ParamHairFront = "HairFront"
    ParamHairSide = "HairSide"


class _FakeMotionPriority:
    FORCE = 3
    IDLE = 1
    NORMAL = 2


class _FakeModel:
    """记录 SetParameterValue 调用，并提供手动演示所需的 no-op 模型方法。"""

    def __init__(self):
        self.calls = []

    def SetParameterValue(self, pid, val, weight=1.0):
        self.calls.append((pid, float(val), float(weight)))

    def StopAllMotions(self):
        self.calls.append(("StopAllMotions",))

    def ResetExpressions(self):
        self.calls.append(("ResetExpressions",))

    def SetExpression(self, name):
        self.calls.append(("SetExpression", name))

    def GetMotions(self):
        return {"": [{"File": "idle.motion3.json"}]}

    def StartMotion(self, group, idx, prio):
        self.calls.append(("StartMotion", group, idx, prio))


def _make_renderer():
    r = Live2DRenderer(parent=None)
    r._live2d = SimpleNamespace(StandardParams=_FakeParams, MotionPriority=_FakeMotionPriority)
    r._model = _FakeModel()
    r._proc_last_t = time.monotonic() - 0.033
    r._auto_motion_min_s = 30.0
    r._auto_motion_max_s = 80.0
    r._auto_motion_next_at = 0.0
    r.set_master_emotion("happy")
    return r


def _enter_override(r):
    r._manual_override = True
    r._manual_override_at = time.monotonic()


def test_override_not_active_by_default():
    r = _make_renderer()
    assert r._in_manual_override() is False
    r._update_procedural_emotion()
    pids = {c[0] for c in r._model.calls}
    # 默认状态下面部参数正常写入
    assert {"EyeLSmile", "BrowLAngle", "MouthForm"} <= pids


def test_override_skips_facial_but_keeps_natural():
    """锁定期间跳过眼/眉/嘴/脸红，但呼吸/眼神/头发照常写。

    这是关键回归：若 `_override` 未定义，方法内 NameError 被方法级
    except 吞掉，所有参数都不会写（包括自然律动），此用例必然失败。
    """
    r = _make_renderer()
    _enter_override(r)
    assert r._in_manual_override() is True
    r._update_procedural_emotion()
    pids = {c[0] for c in r._model.calls}
    # 面部/嘴/脸红被跳过
    assert "EyeLSmile" not in pids
    assert "BrowLAngle" not in pids
    assert "MouthForm" not in pids
    assert "MouthOpenY" not in pids
    # 自然律动仍写：呼吸/眼神/头发
    assert "Breath" in pids
    assert "EyeBallX" in pids
    assert "HairFront" in pids


def test_set_expression_by_name_enters_override():
    r = _make_renderer()
    r.set_expression_by_name("比心")
    assert r._manual_override is True
    assert r._in_manual_override() is True
    # 清场动作被调用（停 motion + 清旧表情 + 设新表情）
    called = {c[0] for c in r._model.calls}
    assert "StopAllMotions" in called
    assert "ResetExpressions" in called
    assert ("SetExpression", "比心") in r._model.calls


def test_force_idle_clears_override():
    r = _make_renderer()
    _enter_override(r)
    assert r._in_manual_override() is True
    r._force_idle()
    assert r._manual_override is False
    assert r._manual_override_at == 0.0
    assert r._in_manual_override() is False


def test_override_expires_after_timeout():
    r = _make_renderer()
    r._manual_override = True
    r._manual_override_at = time.monotonic() - (r.MANUAL_OVERRIDE_TIMEOUT + 1.0)
    assert r._in_manual_override() is False
    # 过期后程序化层恢复正常面部写入
    r._update_procedural_emotion()
    pids = {c[0] for c in r._model.calls}
    assert "EyeLSmile" in pids


def test_set_emotion_yields_during_override():
    """锁定期间 set_emotion 只同步标签，不重播 motion / 不设表情。"""
    r = _make_renderer()
    r._motion_history = getattr(r, "_motion_history", [])
    _enter_override(r)
    before = list(r._model.calls)
    r.set_emotion("sad")
    assert r._current_emotion == "sad"
    assert r._emotion_target == "sad"
    # 锁定让位：不应有任何新的模型调用（无 motion、无表情参数写入）
    assert r._model.calls == before


def test_force_idle_resets_auto_motion_timer():
    """_force_idle 回到 idle 后，自动随机动作计时器应推到未来。

    根因②：_tick_auto_motion 在手动演示锁定期直接 return 不更新
    _auto_motion_next_at，计时器停在"过去"；一旦锁定解除（如点重置）就
    立即重播随机手势，视觉上"重置没生效"。_force_idle 必须把计时器推到
    未来一个完整间隔，让桌宠重置后静止休息。
    """
    r = _make_renderer()
    # 模拟演示锁定期：_auto_motion_next_at 停在很久以前
    r._auto_motion_next_at = time.monotonic() - 100.0
    _enter_override(r)  # 锁定（与真实"动作展示"场景一致）
    r._force_idle()
    assert r._manual_override is False, "force_idle 应清除手动演示锁"
    assert r._auto_motion_next_at > time.monotonic(), \
        "force_idle 后自动动作计时器应推到未来，避免秒级重播手势"
