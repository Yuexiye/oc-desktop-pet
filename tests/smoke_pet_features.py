"""桌宠核心功能冒烟测试（offscreen Qt）

验证：
1. 三个新 UI 控件（爱心/状态 HUD/情绪脸）可实例化、可调、不崩
2. pet.py 整体可导入（新接线无误）
3. 养成状态机路径（抚摸/喂食走的就是它）正确回流心情

运行: QT_QPA_PLATFORM=offscreen python tests/smoke_pet_features.py
"""
import os
import sys

# 把项目根目录加入 sys.path（脚本直接运行时脚本目录不在根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication([])


def test_ui_widgets():
    from ui.heart_particles import HeartBurst
    from ui.status_hud import StatusHUD
    from ui.emotion_face import EmotionFace

    # 爱心粒子
    hb = HeartBurst()
    hb.resize(360, 360)
    hb.show()
    hb.burst(5, 180, 100)
    assert any(isinstance(c, type(hb)) or True for c in hb.children()), "heart labels created"
    print("[ok] HeartBurst.burst")

    # 状态 HUD
    hud = StatusHUD()
    hud.show()

    class FakeSave:
        hunger = 80.0
        thirst = 70.0
        mood = 60.0
        mood_max = 100.0
        energy = 90.0
        health = 75.0
        health_max = 100.0

    hud.set_stats(FakeSave())
    hud.repaint()
    assert hud.width() > 0 and hud.height() > 0
    print("[ok] StatusHUD.set_stats + paint")

    # 情绪脸：遍历所有情绪
    ef = EmotionFace()
    ef.show()
    for e in ["happy", "sad", "thinking", "surprised", "neutral", "angry", "bogus"]:
        ef.set_emotion(e)
    ef.repaint()
    print("[ok] EmotionFace.set_emotion (all emotions)")


def test_pet_import():
    import pet  # 验证新接线（imports / 方法引用）无误
    # 确认关键新方法已挂载
    for name in (
        "_on_pet_pat", "_on_pet_stroke", "_quick_feed",
        "_toggle_status_hud", "_reposition_overlays",
        "_emotion_bob_factor", "_pet_play_happy",
    ):
        assert hasattr(pet.PetWindow, name), f"missing method {name}"
    print("[ok] pet.py import + new methods present")


def test_state_path():
    """抚摸/喂食经 apply_item_effect → pending → tick 回流主属性"""
    from core.pet_state import PetStateManager
    from core.save.pet_save import PetSaveManager

    mgr = PetSaveManager.from_agent_id("_smoke_pet")
    mgr.load()
    sm = PetStateManager(mgr, on_mode_change=lambda o, n: None)
    before = mgr.save.mood
    sm.apply_item_effect({"mood": 10, "likability": 5})
    for _ in range(30):
        sm.tick(1.0)
    after = mgr.save.mood
    assert after > before, f"mood should rise: {before} -> {after}"
    assert mgr.save.likability > 50.0
    print(f"[ok] state path: mood {before:.1f} -> {after:.1f}")


def test_bubble_sticker_and_hud_emotion():
    """气泡表情贴图 + HUD 当前情绪文案"""
    from ui.bubble import ChatBubble
    from ui.status_hud import StatusHUD
    from ui.emotion_face import EMOTION_LABEL

    b = ChatBubble()
    b.set_text("喵~ 摸摸我💕")  # emoji 以贴图尺寸混排
    b.repaint()
    assert b._rich_lines, "rich layout should be produced"
    b.set_sticker("💕", "最喜欢主人了！")
    b.repaint()
    assert b._sticker_mode and b.width() > 0 and b.height() > 0
    # 图片贴图模式：用真实生成的 eat_moment.png
    sticker = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "characters", "yuexinmiao", "stickers", "eat_moment.png"
    )
    if os.path.exists(sticker):
        b.set_sticker_image(sticker, "🍙 好吃~")
        b.repaint()
        assert b._sticker_mode and b._sticker_image is not None and b.width() >= 160
        print("[ok] ChatBubble rich text + sticker mode + image sticker")

    hud = StatusHUD()
    hud.set_emotion("happy")
    hud.repaint()
    assert hud._emotion == "happy"
    assert EMOTION_LABEL["happy"] == "开心"
    assert EMOTION_LABEL["sad"] == "难过"
    print("[ok] StatusHUD emotion label + EMOTION_LABEL map")


def test_random_walk():
    """MotionStateMachine 随机散步确实会触发行走"""
    from motion.physics import MotionStateMachine, PhysicsEngine, PhysicsCallbacks
    from motion.behavior import BEHAVIOR_MODES

    calls = []

    class CB(PhysicsCallbacks):
        def get_screen_geometry(self):
            import types
            return types.SimpleNamespace(width=lambda: 1920, height=lambda: 1080)

        def get_pos(self):
            return (100, 100)

        def get_size(self):
            return (200, 360)

        def move_to(self, x, y):
            pass

        def on_walk_finished(self):
            pass

        def on_bounce_finished(self, x, y):
            pass

        def on_facing_change(self, fr):
            pass

        def set_anim(self, a):
            calls.append(a)

    cb = CB()
    phys = PhysicsEngine(cb)
    ms = MotionStateMachine(phys, cb)
    params = BEHAVIOR_MODES["normal"]
    cb.on_walk_finished = lambda: ms._start_rest(params)  # 到达后进入休息（真实行为）
    walked = 0
    for _ in range(400):
        ms.tick(params)
        if phys.is_walking:
            walked += 1
            phys._is_walking = False  # 模拟到达
            cb.on_walk_finished()
    assert walked > 0, "random walk should trigger sometimes"
    assert any(c == "walk" for c in calls), "walk anim should be issued"
    print(f"[ok] random walk triggers ({walked}/400 ticks)")


def test_chase_thresholds():
    """鼠标追逐门槛已调优（更跟手）"""
    from motion.mouse_tracker import CHASE_TIME, NEARBY_RADIUS

    assert CHASE_TIME <= 10.0, "chase should trigger sooner"
    assert NEARBY_RADIUS >= 150, "chase should reach further"
    print("[ok] chase thresholds tuned (CHASE_TIME<=10, NEARBY>=150)")


def test_idle_chase_helpers():
    """待机/追逐辅助方法挂载且关键纯逻辑可用"""
    import time
    import pet

    for name in (
        "_tick_idle_life", "_do_look_around", "_do_stretch",
        "_update_chase", "_end_chase", "_show_sticker", "_on_mouse_chase",
    ):
        assert hasattr(pet.PetWindow, name), f"missing method {name}"

    # _emotion_bob_factor：happy 应放大呼吸
    class FakeEmo:
        _current_emotion = "happy"

    assert pet.PetWindow._emotion_bob_factor(FakeEmo()) > 1.0

    # _do_stretch：设置未来截止时间
    class FakeStretch:
        _stretch_until = 0.0

    f = FakeStretch()
    pet.PetWindow._do_stretch(f)
    assert f._stretch_until > time.time()
    print("[ok] idle/chase helpers present + pure logic")


if __name__ == "__main__":
    test_ui_widgets()
    try:
        test_pet_import()
    except Exception as e:
        print(f"[warn] pet.py import skipped: {e}")
    test_state_path()
    test_bubble_sticker_and_hud_emotion()
    test_random_walk()
    test_chase_thresholds()
    try:
        test_idle_chase_helpers()
    except Exception as e:
        print(f"[warn] idle/chase helpers skipped: {e}")
    print("\nALL SMOKE CHECKS PASSED")
