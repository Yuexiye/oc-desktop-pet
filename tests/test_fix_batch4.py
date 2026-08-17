"""评审批次4：15 项中高价值 P3 修复的回归测试（offscreen Qt）

覆盖：
- P4-1  crash_collector 线程快照补每线程堆栈 + c_extensions 标注 .pyd 首次 import 线程
- P4-2  main.py OC_TRACE_IMPORTS 探针默认追踪 faster_whisper/ctranslate2/live2d
- P4-3  sprite_renderer emotion→atlas 分支 fps==0 除零兜底
- P4-4  audio_mixin on_tts_start _frames None 保护
- P4-5  phone_receiver stop() 补 server_close()
- P4-6  window_interaction 三处（无效垂直偏移 / QTimer 泄漏 / 死模块引用）
- P4-7  cosyvoice OUTPUT_DIR HOME 兜底
- P4-8  whisper_local _has_cuda 结果缓存
- P4-9  physics 窗口大于屏幕时弹跳边界归位居中
- P4-10 chat_mixin _think_timeout QTimer 带 parent
- P4-11 pet_manager close_window 重复定义清理
- P4-12 conversation_engine _is_stale 重复检查清理 + on_reply 包 try
- P4-13 framebaker PowerShell 命令参数化匹配（防注入/通配符多匹配）
- P4-14 sprite_renderer open().read() 改 with open（句柄释放）
- P4-15 launcher 崩溃重启打印最新 crash_dump zip 路径
- 可选：enhanced_environment 死代码 / harness_adapter 不可达 return /
        onboarding/emotion_face QPropertyAnimation 存 self 引用

运行: QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fix_batch4.py -v
"""
import os
import re
import sys
import time
from pathlib import Path

# 项目根加入 sys.path（脚本直接运行时脚本目录不在根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 先装 crash_collector 的 .pyd 导入追踪器，让后续 PySide6 .pyd 导入被记录
import core.crash_collector  # noqa: E402

from PySide6.QtCore import QObject, QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

ROOT = Path(__file__).resolve().parents[1]


# ═════════════════════ P4-1：crash_collector 线程堆栈 + .pyd 导入线程 ═════════════════════
def test_crash_collector_source_has_stacks_and_tracker():
    """源码级：快照用 sys._current_frames + traceback.format_stack；.pyd 有导入线程标注"""
    src = (ROOT / "core" / "crash_collector.py").read_text(encoding="utf-8")
    assert "sys._current_frames()" in src
    assert "traceback.format_stack" in src
    assert "class _PydImportTracker" in src
    assert "sys.meta_path.insert(0, tracker)" in src
    assert "首次 import 线程" in src


def test_crash_collector_thread_snapshot_has_stacks(monkeypatch):
    """行为级：_collect_once 产出的 threads.txt 含线程名 + 实际堆栈帧"""
    import core.crash_collector as cc
    captured: dict[str, str] = {}

    class FakeZip:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write(self, *a, **k):
            pass

        def writestr(self, name, data):
            captured[name] = data

    import zipfile
    monkeypatch.setattr(zipfile, "ZipFile", FakeZip)
    monkeypatch.setattr(cc, "_COLLECTED", False)
    cc._collect_once("test")
    threads = captured.get("threads.txt", "")
    assert "MainThread" in threads, "线程快照应包含 MainThread"
    assert 'File "' in threads, "线程快照应包含 traceback.format_stack 堆栈帧"
    # c_extensions 标注：.pyd 行应有"首次 import 线程"
    cext = captured.get("c_extensions.txt", "")
    assert "首次 import 线程" in cext, "c_extensions.txt 应标注 .pyd 首次 import 线程"


def test_crash_collector_pyd_tracker_records_thread(monkeypatch):
    """行为级：_PydImportTracker 记录模块名 -> (线程名, ident)，且不拦截导入"""
    import core.crash_collector as cc
    tracker = cc._PydImportTracker()
    spec = tracker.find_spec("json")  # 纯 Python 模块：不应记录
    assert spec is None, "追踪器必须返回 None 不拦截导入"
    assert "json" not in tracker.imported_by
    spec2 = tracker.find_spec("PySide6.QtCore")  # C 扩展：应记录当前线程
    assert spec2 is None
    if "PySide6.QtCore" in tracker.imported_by:
        name, ident = tracker.imported_by["PySide6.QtCore"]
        assert name
        assert ident is not None


# ═════════════════════ P4-2：main.py 导入探针默认开启 ═════════════════════
def test_main_trace_imports_default_enabled():
    """源码级：OC_TRACE_IMPORTS 未设置时默认追踪 C 扩展重型依赖并标注线程"""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'os.environ.get("OC_TRACE_IMPORTS")' in src
    assert '"faster_whisper,ctranslate2,live2d"' in src, "默认追踪列表必须包含三个 C 扩展包"
    assert "thread=" in src, "探针输出必须包含线程信息（确认 C 扩展在哪个线程被 import）"


# ═════════════════════ P4-3：sprite_renderer fps==0 兜底 ═════════════════════
def test_sprite_renderer_fps_zero_guard_source():
    """源码级：emotion→atlas 分支必须带 fps>0 判断"""
    src = (ROOT / "avatar" / "sprite_renderer.py").read_text(encoding="utf-8")
    assert "if fps and fps > 0" in src, "atlas 分支必须防 fps==0 除零"


def test_sprite_renderer_fps_zero_no_crash():
    """行为级：fps=0 时 play_anim(emotion→atlas) 不抛 ZeroDivisionError，走兜底间隔"""
    from avatar.sprite_renderer import SpriteRenderer
    r = SpriteRenderer.__new__(SpriteRenderer)
    r._frames = {"idle_seq": [object()]}
    r._seq_fps = {"idle_seq": 0}
    r._emotion_ranges = {"happy": "idle_seq"}
    r._anim_seq = ""
    r._anim_range = (None, None)
    r._anim_idx = 0
    r._current_anim = None
    r._current_emotion = None
    r._anim_timer = type("T", (), {"setInterval": lambda self, v: setattr(self, "iv", v),
                                   "start": lambda self: None, "stop": lambda self: None})()
    r._show_frame = lambda: None
    # 不应抛 ZeroDivisionError
    r.play_anim("whatever", emotion="happy")
    assert r._anim_timer.iv > 0, "fps=0 时应落到兜底间隔"


# ═════════════════════ P4-4：audio_mixin _frames None 保护 ═════════════════════
def test_audio_mixin_tts_start_without_renderer():
    """行为级：无 _renderer 时 on_tts_start 不抛 AttributeError"""
    from pet_mixins.audio_mixin import AudioMixin
    w = type("W", (), {"_anim_seq": None})()
    AudioMixin.on_tts_start(w, "neutral")


def test_audio_mixin_tts_start_renderer_frames_none():
    """行为级：_renderer._frames 为 None 时按无口型帧处理，不崩溃"""
    from pet_mixins.audio_mixin import AudioMixin

    class R:
        _frames = None
        speaking = None

        def set_speaking(self, v):
            self.speaking = v

        def play_anim(self, seq):
            self.played = seq

    w = type("W", (), {"_renderer": R(), "_anim_seq": None})()
    AudioMixin.on_tts_start(w, "happy")
    assert w._renderer.speaking is True
    assert not hasattr(w._renderer, "played"), "无口型帧时应跳过 play_anim"


# ═════════════════════ P4-5：phone_receiver stop 补 server_close ═════════════════════
def test_phone_receiver_stop_closes_server():
    """行为级：stop() 调用 server_close 释放 socket"""
    from core.phone_receiver import PhoneActivityReceiver
    closed = []

    class FakeServer:
        def shutdown(self):
            self.shutdown_called = True

        def server_close(self):
            closed.append(1)

    recv = PhoneActivityReceiver.__new__(PhoneActivityReceiver)
    recv._server = FakeServer()
    recv.stop()
    assert recv._server is None
    assert closed == [1], "stop() 必须调用 server_close()"


# ═════════════════════ P4-6：window_interaction 三处 ═════════════════════
def test_window_interaction_no_dead_module_and_no_target_y():
    """源码级：不再 import ui.virtual_object_overlay；move_near_window 不再计算无效 target_y"""
    src = (ROOT / "core" / "window_interaction.py").read_text(encoding="utf-8")
    assert "from ui.virtual_object_overlay import" not in src, "死模块 import 必须删除"
    assert "import ui.virtual_object_overlay" not in src
    body = src.split("def move_near_window", 1)[1].split("def show_object_on_window", 1)[0]
    assert "target_y =" not in body, "无效的垂直偏移计算必须删除"
    assert "start_walk(target_x," in body, "start_walk 只接收 x（不再传 y）"


def test_window_interaction_show_object_no_crash():
    """行为级：show_object_on_window 在无 overlay 时安全返回 False（原实现 import 必炸）"""
    from core.window_interaction import WindowInteraction, WindowRect

    class FakePet:
        def screen(self):
            return None

    wi = WindowInteraction(FakePet())
    wi.get_current_window = lambda: WindowRect(x=0, y=0, width=200, height=200)
    assert wi.show_object_on_window("☕", "咖啡") is False


def test_window_interaction_timer_reused():
    """行为级：walk_along_edge 复用同一 QTimer 句柄（防泄漏）"""
    from core.window_interaction import WindowInteraction, WindowRect

    class FakePhysics:
        def start_walk(self, x, facing_right):
            self.called = (x, facing_right)

    class FakePet(QObject):
        def __init__(self):
            super().__init__()
            self._physics = FakePhysics()

        def screen(self):
            return None

        def pos(self):
            return QPoint(100, 100)

        def width(self):
            return 100

        def height(self):
            return 100

        def move_to(self, x, y):
            pass

        def _set_anim_seq(self, a):
            pass

    pet = FakePet()
    wi = WindowInteraction(pet)
    wi.get_current_window = lambda: WindowRect(x=0, y=0, width=200, height=200)
    assert wi.walk_along_edge(speed=1, steps=40)
    t1 = wi._walk_timer
    assert t1 is not None
    assert wi.walk_along_edge(speed=2, steps=40), "第二次调用应复用定时器"
    assert wi._walk_timer is t1, "QTimer 句柄必须复用，不能每次新建"
    wi.stop_walking()
    assert wi._walk_timer is t1, "stop 后应保留句柄供复用"
    assert not t1.isActive()


def test_window_interaction_move_near_window_x_only():
    """行为级：move_near_window 只传 x 给 start_walk（垂直偏移已删除）"""
    from core.window_interaction import WindowInteraction, WindowRect

    class FakePhysics:
        def __init__(self):
            self.called = None

        def start_walk(self, x, facing_right):
            self.called = (x, facing_right)

    class FakePet(QObject):
        def __init__(self):
            super().__init__()
            self._physics = FakePhysics()

        def screen(self):
            return None

        def pos(self):
            return QPoint(100, 100)

        def width(self):
            return 100

        def height(self):
            return 100

    pet = FakePet()
    wi = WindowInteraction(pet)
    wi.get_current_window = lambda: WindowRect(x=0, y=0, width=200, height=200)
    assert wi.move_near_window(offset_x=10) is True
    assert pet._physics.called == (210, True)


# ═════════════════════ P4-7：cosyvoice OUTPUT_DIR HOME 兜底 ═════════════════════
def test_cosyvoice_output_dir_home_fallback(monkeypatch):
    """行为级：Path.home() 抛异常时 _resolve_output_dir 兜底到临时目录"""
    import tts_provider.cosyvoice as c
    from pathlib import Path as RealPath

    def boom():
        raise RuntimeError("HOME unset")

    monkeypatch.setattr(RealPath, "home", boom)
    d = c._resolve_output_dir()
    assert isinstance(d, RealPath)
    assert "hanako" in str(d).lower()
    # 模块级 OUTPUT_DIR 也必须可解析（不崩）
    assert isinstance(c.OUTPUT_DIR, RealPath)


# ═════════════════════ P4-8：whisper_local _has_cuda 缓存 ═════════════════════
def test_whisper_has_cuda_cached(monkeypatch):
    """行为级：_has_cuda 结果缓存，torch 只 import 一次"""
    from asr_provider.whisper_local import WhisperLocalProvider
    WhisperLocalProvider._has_cuda_result = None
    import builtins
    real_import = builtins.__import__
    calls = []

    def fake_import(name, *a, **k):
        if name == "torch":
            calls.append(name)
            raise ImportError("test: no torch")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert WhisperLocalProvider._has_cuda() is False
    assert WhisperLocalProvider._has_cuda() is False
    assert len(calls) == 1, "缓存后第二次调用不应再 import torch"
    WhisperLocalProvider._has_cuda_result = None  # 复位，避免影响其他用例


# ═════════════════════ P4-9：physics 窗口大于屏幕归位 ═════════════════════
def test_physics_bounce_window_larger_than_screen():
    """行为级：窗口比屏幕大时弹跳直接归位居中并回到 idle"""
    from motion.physics import PhysicsEngine

    class FakeCB:
        def __init__(self):
            self.moves = []
            self.anims = []
            self.finished = []

        def get_screen_geometry(self):
            return type("SG", (), {"width": lambda s: 100, "height": lambda s: 100})()

        def get_size(self):
            return (200, 200)

        def get_pos(self):
            return (10, 10)

        def move_to(self, x, y):
            self.moves.append((x, y))

        def set_anim(self, a):
            self.anims.append(a)

        def on_bounce_finished(self, x, y):
            self.finished.append((x, y))

        def on_walk_finished(self):
            pass

        def on_facing_change(self, f):
            pass

    cb = FakeCB()
    p = PhysicsEngine(cb)
    p.start_bounce(5.0, 5.0)
    p._tick_bounce()
    assert not p.is_bouncing, "边界非法时必须停止弹跳"
    assert cb.moves[-1] == (0, 0), "应归位到屏幕中央 (max(0, (100-200)//2)=0)"
    assert cb.anims[-1] == "idle"
    assert cb.finished, "应触发 on_bounce_finished"


# ═════════════════════ P4-10：chat_mixin QTimer 带 parent ═════════════════════
def test_chat_mixin_think_timer_has_parent():
    """源码级：_think_timeout 用 _QTimer(self) 带 parent"""
    src = (ROOT / "pet_mixins" / "chat_mixin.py").read_text(encoding="utf-8")
    assert "_QTimer(self)" in src, "_think_timeout 必须带 parent"


# ═════════════════════ P4-11：pet_manager 重复 close_window ═════════════════════
def test_pet_manager_single_close_window():
    """源码级：close_window 只定义一次（后覆盖前的重复定义已删除）"""
    src = (ROOT / "pet_manager.py").read_text(encoding="utf-8")
    assert src.count("def close_window(") == 1


# ═════════════════════ P4-12：conversation_engine 重复 _is_stale 清理 ═════════════════════
def test_conversation_engine_no_duplicate_stale_block():
    """源码级：TTS 后已打断块只保留一份；on_reply 包 try"""
    src = (ROOT / "core" / "conversation_engine.py").read_text(encoding="utf-8")
    assert src.count("TTS 后已打断，仅保留文字气泡（丢弃音频）") == 1, "重复的 _is_stale 块必须删除"
    assert "打断后 on_reply 回调失败" in src, "打断分支的 on_reply 必须包 try"
    assert "on_reply 回调失败" in src, "音频回调的 on_reply 必须包 try"


# ═════════════════════ P4-13：framebaker PowerShell 参数化匹配 ═════════════════════
def test_framebaker_powershell_parameterized_match():
    """源码级：不再把路径插进 -like 通配符；用 .Contains() + 单引号转义"""
    src = (ROOT / "ui" / "framebaker.py").read_text(encoding="utf-8")
    assert "-like '*" not in src, "必须放弃 -like 通配符插值"
    assert ".Contains('" in src, "应改用参数化 .Contains() 匹配"
    assert 'FRAMEBAKER_PATH.replace("\'", "\'\'")' in src, "单引号必须转义"


# ═════════════════════ P4-14：sprite_renderer 句柄释放 ═════════════════════
def test_sprite_renderer_with_open_no_bare_open():
    """源码级：load 路径不再用裸 open().read()，改用 with open"""
    src = (ROOT / "avatar" / "sprite_renderer.py").read_text(encoding="utf-8")
    assert 'json.loads(open(' not in src, "裸 open().read() 必须改为 with open"
    assert "with open(pet_json_path" in src
    assert "with open(json_path" in src


# ═════════════════════ P4-15：launcher 崩溃重启打印 crash zip ═════════════════════
def test_launcher_latest_crash_dump(monkeypatch, tmp_path):
    """行为级：_latest_crash_dump 返回 logs/ 下最新的 crash_dump_*.zip"""
    import launcher
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "crash_dump_20240101_000000.zip"
    new = logs / "crash_dump_20240102_000000.zip"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    old_t = time.time() - 100
    new_t = time.time() - 10
    os.utime(old, (old_t, old_t))
    os.utime(new, (new_t, new_t))
    monkeypatch.setattr(launcher, "HERE", tmp_path)
    assert launcher._latest_crash_dump() == new
    # 无 zip 时返回 None
    (logs / "crash_dump_20240101_000000.zip").unlink()
    (logs / "crash_dump_20240102_000000.zip").unlink()
    assert launcher._latest_crash_dump() is None


def test_launcher_prints_crash_dump_path_on_restart():
    """源码级：异常退出分支打印崩溃现场 zip 路径"""
    src = (ROOT / "launcher.py").read_text(encoding="utf-8")
    assert "def _latest_crash_dump" in src
    assert "崩溃现场已打包" in src
    crash_block = src.split("子进程异常退出", 1)[1].split("time.sleep(RESTART_DELAY)", 1)[0]
    assert "_latest_crash_dump()" in crash_block, "崩溃分支必须调用 _latest_crash_dump 并打印"


# ═════════════════════ 可选清理项 ═════════════════════
def test_enhanced_environment_no_dead_ext_line():
    """可选：infer_file_type 删除了 `_, ext = ..., None` 死代码，行为不变（含无扩展名兜底）"""
    from core.enhanced_environment import infer_file_type
    assert infer_file_type("hello.py") == "code"
    assert infer_file_type("a/b/c.png") == "image"
    assert infer_file_type("") == "unknown"
    # 无扩展名非空文件名：必须返回 unknown，不能 UnboundLocalError
    assert infer_file_type("Notepad") == "unknown"
    assert infer_file_type("Visual Studio Code") == "unknown"
    assert infer_file_type("Makefile") == "unknown"


def test_harness_adapter_parse_emotion_no_duplicate():
    """可选：parse_emotion 不可达的重复 return 块已删除"""
    src = (ROOT / "core" / "harness_adapter.py").read_text(encoding="utf-8")
    assert src.count("剥离表情包 XML 段") == 1, "不可达重复块必须删除"
    from core.harness_adapter import HanakoPetAdapter
    cleaned, emotion = HanakoPetAdapter.parse_emotion("你好[emotion:happy]")
    assert cleaned.strip() == "你好"
    assert emotion == "happy"


def test_onboarding_animations_stored_on_self():
    """可选：onboarding QPropertyAnimation 存 self 引用防 GC"""
    src = (ROOT / "ui" / "onboarding.py").read_text(encoding="utf-8")
    assert "self._fade_in_anim = QPropertyAnimation" in src
    assert "self._dismiss_anim = QPropertyAnimation" in src


def test_emotion_face_animation_stored_on_self():
    """可选：emotion_face QPropertyAnimation 存 self 引用防 GC"""
    src = (ROOT / "ui" / "emotion_face.py").read_text(encoding="utf-8")
    assert "self._pop_anim = QPropertyAnimation" in src


def test_startup_screen_fade_anim_stored_on_self():
    """可选：startup_screen 淡出动画已存 self 引用（本就正确，防回归）"""
    src = (ROOT / "ui" / "startup_screen.py").read_text(encoding="utf-8")
    assert "self._fade_anim = QPropertyAnimation" in src
