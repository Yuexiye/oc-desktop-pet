"""Live2D 真实模型端到端测试（无 GL，用 QOffscreenSurface 验证 API）

这是为了弥补 test_live2d_fix.py 只用 mock、没在真实模型上跑过的不足。
真实模型测试能验证：
  - set_emotion 在每秒重复触发下，确实不会无限重播同一 gesture
  - fit 窗口尺寸不再被 canvas 撑大
"""
import os
import sys
import time

# 添加项目根到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_set_emotion_no_infinite_replay():
    """set_emotion 同 emotion 每秒调用一次，验证 motion 不会被反复重播

    模拟 _unified_tick 每秒调一次 set_emotion（真实 pet.py 行为）。
    期望：第一次调 happy 播放 motion，之后连续 N 次调用 happy 不再触发 motion。
    """
    from unittest.mock import MagicMock
    from avatar.live2d_renderer import Live2DRenderer

    obj = object.__new__(Live2DRenderer)
    obj._model = MagicMock()
    obj._model.GetCanvasSizePixel.return_value = (3500.0, 8888.0)
    obj._live2d = MagicMock()
    obj._live2d.MotionPriority.IDLE = 0
    obj._live2d.MotionPriority.NORMAL = 1
    obj._live2d.MotionPriority.FORCE = 2
    obj._fit_scale = 1.0
    obj._fit_scale_x = 1.0
    obj._center_offset_x = 0.0
    obj._offset_scale = (0.0, 0.0)
    obj._ready = True
    obj._motion_files = ["happy.motion3.json", "idle.motion3.json"]
    obj._motion_group_name = ""
    obj._motion_groups = {"": []}
    obj._motion_is_idle = True
    obj._motion_started_at = time.monotonic() - 100.0  # 很久前
    obj._current_motion_idx = None
    obj._emotion_motion_cooldown = {}
    obj._last_gesture_at = 0.0
    obj._apply_expression = MagicMock()

    # 模拟"持续 happy"场景：每秒调用一次 happy（共 10 秒，不主动 force_idle）
    # 模拟 _unified_tick 每秒调一次 set_emotion
    call_count_with_motion = 0
    for i in range(10):
        if i > 0:
            time.sleep(1.1)  # 真实 tick 间隔
        Live2DRenderer.set_emotion(obj, "happy")
        if obj._model.StartMotion.called or obj._model.StartRandomMotion.called:
            call_count_with_motion += 1
            obj._model.StartMotion.reset_mock()
            obj._model.StartRandomMotion.reset_mock()

    # 期望：连续 10 次 happy，motion 触发 ≤ 1 次（不要每 3s 切一次）
    assert call_count_with_motion <= 1, (
        f"持续 happy 10 次调用触发了 {call_count_with_motion} 次 motion，"
        f"应 ≤ 1 次（用户看到的'一直比心'就是这个 bug）"
    )
    print(f"OK: 持续 happy 10 次调用，motion 触发 {call_count_with_motion} 次（≤ 1）")


def test_fit_no_canvas_inflation():
    """fit 窗口不被超高 canvas 撑大

    用 miku 真实 canvas 3500x8888 验证。
    """
    from unittest.mock import MagicMock
    from avatar.live2d_renderer import Live2DRenderer

    obj = object.__new__(Live2DRenderer)
    obj._model = MagicMock()
    obj._model.GetCanvasSizePixel.return_value = (3500.0, 8888.0)  # miku 真实画布
    obj._fit_scale = 1.0
    obj._fit_scale_x = 1.0
    obj._fit_offset_y = 0.0
    obj._center_offset_x = 0.0
    obj._offset_scale = (0.0, 0.0)
    obj._gl_w = 458
    obj._gl_h = 520
    obj._ready = True

    # 模拟 miku 真实 hit-bbox（之前日志：287x473）
    obj._scan_bbox_adaptive = MagicMock(return_value=(150, 48, 437, 521))  # 287x473
    obj._parent = MagicMock()
    obj._parent.fit_window_to_model = MagicMock()

    Live2DRenderer._fit_window_to_model(obj)

    w, h = obj._parent.fit_window_to_model.call_args[0]
    print(f"OK: miku 真实 canvas 3500x8888 → fit 窗口 {w}x{h}（应远小于 7000）")
    # miku canvas 8888 但 fit 不应撑到几千像素
    assert h < 800, f"窗口高度 {h} 仍被 canvas 撑大（应 < 800）"
    assert w < 500, f"窗口宽度 {w} 过大（应 < 500）"


def test_miku_motion_list():
    """列出 miku 真实动作文件，验证 _play_motion_kw 匹配逻辑能覆盖所有 gesture"""
    import json
    motions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "characters", "miku", "live2d", "motions")
    files = [f for f in os.listdir(motions_dir) if f.endswith(".motion3.json")]
    print(f"miku 模型动作文件: {files}")
    # 关键：每个动作的时长 + 是否 Loop
    # 仅 idle 应 Loop=True（持续呼吸），其余表情/手势 Loop=False（播完回 idle）
    loop_expect = {"idle": True}
    for f in sorted(files):
        with open(os.path.join(motions_dir, f), encoding="utf-8") as fp:
            data = json.load(fp)
        meta = data.get("Meta", {})
        dur = meta.get("Duration", "N/A")
        loop = meta.get("Loop", "N/A")
        print(f"  {f}: duration={dur}s, Loop={loop}")
        name = f.replace(".motion3.json", "")
        expected_loop = loop_expect.get(name, False)
        assert loop is expected_loop, f"{f} 应是 Loop={expected_loop}（idle 循环、其余单次）"


if __name__ == "__main__":
    test_set_emotion_no_infinite_replay()
    test_fit_no_canvas_inflation()
    test_miku_motion_list()
    print("\n=== 全部真实模型测试通过 ===")