"""5 个 bugfix 的针对性回归测试（offscreen Qt）

对应未提交改动：
1. ui/gacha_reveal.py  — GachaReveal/GachaRevealMulti 在 __init__ 预创建 `_op_out`，
   修复 gacha_mixin 在 show() 后连接 `_op_out.finished` 时的 AttributeError 演出降级。
2. ui/settings_dialog.py — `_save()` 末尾 `save_config(self._config)` 落盘。
3. avatar/live2d_renderer.py — `_note_motion_started(fname, is_idle)` 改为显式 is_idle，
   修复比心/挥手手势被错判为 idle 而永久卡住。
4. core/conversation_engine.py — TTS 未就绪/合成失败新增告警日志（仅日志层，本测试覆盖其数据源）。
5. tts_provider/edge_tts.py — 新增 `last_error` 属性供引擎层读取。

运行: QT_QPA_PLATFORM=offscreen python -m pytest tests/test_bugfix_regression.py -v
"""
import json
import os
import sys
import tempfile
import types

# 项目根加入 sys.path（脚本直接运行时脚本目录不在根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QPropertyAnimation  # noqa: E402

_app = QApplication.instance() or QApplication([])


# ───────────────────────── 1. gacha_reveal：_op_out 预创建 ─────────────────────────
def _make_single_reveal():
    from ui.gacha_reveal import GachaReveal
    r = GachaReveal(icon="🎁", name="神秘物品", rarity_value="common", pity_text=None)
    r._auto.stop()  # 防止自动消失计时器在测试期触发
    return r


def _make_multi_reveal():
    from ui.gacha_reveal import GachaRevealMulti

    class _Rarity:
        def __init__(self, value):
            self.value = value

    class _Item:
        def __init__(self, value, name="物品", icon="🎁"):
            self.rarity = _Rarity(value)
            self.name = name
            self.icon = icon

    items = [_Item("common"), _Item("rare"), _Item("legendary")]
    r = GachaRevealMulti(items, pity_text="保底：已 80 抽")
    r._auto.stop()
    return r


def test_gacha_single_op_out_precreated():
    """构造后即可访问 _op_out（原 bug：gacha_mixin 在 show() 后连接时 AttributeError）"""
    r = _make_single_reveal()
    try:
        assert hasattr(r, "_op_out"), "GachaReveal 必须在 __init__ 预创建 _op_out"
        assert isinstance(r._op_out, QPropertyAnimation)
        # 模拟 gacha_mixin 的 post-show 连接：不应抛 AttributeError（原 bug 即在此处）
        r._op_out.finished.connect(lambda: None)
    finally:
        r.close()
        r.deleteLater()


def test_gacha_single_dismiss_starts_animation():
    """dismiss() 复用 _op_out 启动淡出，不抛异常"""
    r = _make_single_reveal()
    try:
        r.dismiss()
        assert r._op_out.state() == QPropertyAnimation.State.Running
    finally:
        r.close()
        r.deleteLater()


def test_gacha_multi_op_out_precreated():
    """GachaRevealMulti 同样在 __init__ 预创建 _op_out"""
    r = _make_multi_reveal()
    try:
        assert hasattr(r, "_op_out"), "GachaRevealMulti 必须在 __init__ 预创建 _op_out"
        assert isinstance(r._op_out, QPropertyAnimation)
        r._op_out.finished.connect(lambda: None)
        r.dismiss()
        assert r._op_out.state() == QPropertyAnimation.State.Running
    finally:
        r.close()
        r.deleteLater()


# ─────────────────── 3. live2d：_note_motion_started 显式 is_idle ───────────────────
def test_live2d_note_motion_started_explicit_is_idle():
    """is_idle 由调用方显式传入，不再依赖文件名是否含 'idle'。

    原 bug：某手势 motion 文件名恰含 'idle'（如 bixin_idle.motion3.json）被错判为
    idle，导致 GESTURE_TIMEOUT 兜底永不触发、手势永久卡住。新契约：文件名无关，
    以 is_idle 为准。
    """
    from avatar.live2d_renderer import Live2DRenderer

    fake = types.SimpleNamespace(_motion_is_idle=None, _motion_started_at=0.0)
    meth = Live2DRenderer._note_motion_started.__get__(fake)

    # idle 动作（文件名含 idle）→ True
    meth("idle.motion3.json", is_idle=True)
    assert fake._motion_is_idle is True

    # 比心手势文件名恰含 'idle' → 必须 False（这就是修复点）
    meth("bixin_idle.motion3.json", is_idle=False)
    assert fake._motion_is_idle is False

    # 普通挥手手势 → False
    meth("wave.motion3.json", is_idle=False)
    assert fake._motion_is_idle is False

    # 无 'idle' 字样的呼吸动作显式传入 True → True
    meth("breath.motion3.json", is_idle=True)
    assert fake._motion_is_idle is True

    # 起始时间应被重置（卡手势超时兜底用）
    assert fake._motion_started_at > 0.0


# ─────────────────────── 5. edge_tts：last_error 属性 ───────────────────────
def test_edge_tts_last_error_property():
    """新增 last_error 属性，引擎层可在无语音时读取真实失败原因。"""
    from tts_provider.edge_tts import EdgeTtsProvider

    p = EdgeTtsProvider()
    # 初始为空字符串
    assert p.last_error == ""
    # 合成/预检失败时写入（模拟）
    p._last_error = "edge-tts 未安装：pip install edge-tts"
    assert p.last_error == "edge-tts 未安装：pip install edge-tts"


# ─────────── 2. settings_dialog 落盘：save_config 合并不会破坏 per-pet 配置 ───────────
def test_save_config_preserves_per_pet_and_other_keys():
    """验证 settings 修复所依赖的 save_config 为「合并式原子写」：

    - 只更新传入字段（如 tts），不冲掉其他系统键（screen）
    - 不破坏桌宠独立配置 agents[].dialog.agent_id / agents[].tts
    这正是 _save() 末尾新增 save_config(self._config) 必须保证的安全性。
    """
    import config as cfg_mod
    from config import save_config

    d = tempfile.mkdtemp()
    path = os.path.join(d, "config.json")
    existing = {
        "tts": {"enabled": False, "provider": "edge"},
        "agents": [
            {"id": "a", "dialog": {"agent_id": "gpt"}, "tts": {"provider": "edge", "voice": "x"}}
        ],
        "screen": {"enabled": True, "interval": 30},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f)

    old_path = cfg_mod.CONFIG_PATH
    cfg_mod.CONFIG_PATH = path
    try:
        # 模拟 _save() 只改了 tts 部分后调用 save_config
        save_config({"tts": {"enabled": True, "provider": "cosyvoice"}})
        with open(path, "r", encoding="utf-8") as f:
            out = json.load(f)
    finally:
        cfg_mod.CONFIG_PATH = old_path

    # tts 被更新
    assert out["tts"]["enabled"] is True
    assert out["tts"]["provider"] == "cosyvoice"
    # per-pet dialog.agent_id 未被冲掉
    assert out["agents"][0]["dialog"]["agent_id"] == "gpt"
    # per-pet tts 未被冲掉
    assert out["agents"][0]["tts"]["provider"] == "edge"
    # 其他系统键保留
    assert out["screen"]["enabled"] is True


if __name__ == "__main__":
    test_gacha_single_op_out_precreated()
    test_gacha_single_dismiss_starts_animation()
    test_gacha_multi_op_out_precreated()
    test_live2d_note_motion_started_explicit_is_idle()
    test_edge_tts_last_error_property()
    test_save_config_preserves_per_pet_and_other_keys()
    print("\nALL BUGFIX REGRESSION CHECKS PASSED")
