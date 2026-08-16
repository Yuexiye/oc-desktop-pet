"""评审批次1：6 项修复的回归测试（offscreen Qt）

覆盖：
- Fix1 跨线程 TTS 截停：后台线程不得直调 _tts_player（COM 0x8001010D 路径）
- Fix2 对话引擎线程保护 + 有界队列
- Fix3 launcher 业务就绪哨兵
- Fix4 OC_DISABLE_TRAY 下 load_character 判空
- Fix5 main.py excepthook 链式调用（不覆盖 crash_collector 钩子）
- Fix6 CosyVoice 超时脏队列清理

运行: QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fix_batch1.py -v
"""
import collections
import os
import re
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

# 项目根加入 sys.path（脚本直接运行时脚本目录不在根）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

ROOT = Path(__file__).resolve().parents[1]


# ───────────────────────── Fix1：跨线程 TTS 截停 ─────────────────────────
def test_tts_stop_signal_and_slot_present():
    """PetWindow 提供 tts_stop_signal 信号 + _do_tts_stop 主线程槽"""
    import pet
    assert hasattr(pet.PetWindow, "tts_stop_signal")
    assert hasattr(pet.PetWindow, "_do_tts_stop")
    # _init_engine 中必须把信号接到主线程槽（真实接线）
    pet_src = (ROOT / "pet.py").read_text(encoding="utf-8")
    assert "self.tts_stop_signal.connect(self._do_tts_stop)" in pet_src, (
        "_init_engine 缺少 tts_stop_signal -> _do_tts_stop 连接"
    )


def test_do_tts_stop_calls_player_stop():
    """_do_tts_stop 在主线程调用 _tts_player.stop()（未初始化时安全跳过）"""
    import pet
    w = pet.PetWindow.__new__(pet.PetWindow)
    # 未初始化 _tts_player → 不应抛异常
    pet.PetWindow._do_tts_stop(w)
    # 有播放器 → 调用 stop
    calls = []
    w._tts_player = types.SimpleNamespace(stop=lambda: calls.append("stop"))
    pet.PetWindow._do_tts_stop(w)
    assert calls == ["stop"]


def test_tts_stop_signal_cross_thread_delivery():
    """后台线程 emit tts_stop_signal → 队列送达主线程 → 调用 _tts_player.stop()

    直接验证 0x8001010D 修复路径：TTS 截停不再由后台线程直调 COM 组件，
    而是经 Qt 信号绕回主线程执行。
    """
    from PySide6.QtCore import QObject, Signal

    import pet

    class _Host(QObject):
        tts_stop_signal = Signal()

    host = _Host()
    w = pet.PetWindow.__new__(pet.PetWindow)
    calls = []
    w._tts_player = types.SimpleNamespace(stop=lambda: calls.append("stop"))
    host.tts_stop_signal.connect(pet.PetWindow._do_tts_stop.__get__(w))
    threading.Thread(target=host.tts_stop_signal.emit).start()
    deadline = time.time() + 3
    while time.time() < deadline and not calls:
        _app.processEvents()
        time.sleep(0.01)
    assert calls == ["stop"], "后台线程 emit 后必须在主线程调用 _tts_player.stop()"


def test_chat_mixin_asr_background_uses_signal_not_direct_stop():
    """_do_asr（后台线程）不得直调 _tts_player.stop()，必须经 tts_stop_signal"""
    src = (ROOT / "pet_mixins" / "chat_mixin.py").read_text(encoding="utf-8")
    m = re.search(r"def _toggle_voice\(self\):\n(.*?)(?=\n    def )", src, re.S)
    assert m, "未找到 _toggle_voice"
    body = m.group(1)
    asr_block = body.split("def _do_asr()", 1)[1].split("t = threading.Thread", 1)[0]
    assert "self._tts_player.stop()" not in asr_block, (
        "后台线程 _do_asr 内直调 _tts_player.stop() → 跨线程 COM 0x8001010D 路径"
    )
    assert "self.tts_stop_signal.emit()" in asr_block


# ───────────────────────── Fix2：引擎线程保护 + 有界队列 ─────────────────────────
def _make_engine():
    from core.conversation_engine import ConversationEngine
    return ConversationEngine(
        character_id="yuexinmiao",
        perception=MagicMock(),
        tts_provider=MagicMock(),
    )


def test_queue_is_bounded_deque():
    """消息队列改为有界 deque(maxlen=200)：满时丢最旧"""
    engine = _make_engine()
    assert isinstance(engine._queue, collections.deque)
    assert engine._queue.maxlen == 200
    for i in range(250):
        engine.send(f"msg-{i}", character="yuexinmiao")
    assert len(engine._queue) == 200
    assert engine._queue[0]["text"] == "msg-50"    # 最旧 50 条被丢
    assert engine._queue[-1]["text"] == "msg-249"  # 最新保留


def test_interrupt_preserves_deque_semantics():
    """interrupt() 清非用户消息后 _queue 仍是 deque（不退化回 list）"""
    engine = _make_engine()
    engine.send("user-a", character="yuexinmiao")
    engine.send("proactive-b", character="yuexinmiao", source="proactive")
    engine.send("user-c", character="yuexinmiao")
    engine.interrupt(reason="new_message")
    assert isinstance(engine._queue, collections.deque)
    assert engine._queue.maxlen == 200
    texts = [m["text"] for m in engine._queue]
    assert "proactive-b" not in texts  # proactive 被清掉
    assert "user-a" in texts and "user-c" in texts  # 用户消息保留


def test_run_survives_message_exception():
    """主循环内单条消息异常：记录堆栈后继续，引擎线程不死亡"""
    engine = _make_engine()
    # 替换真实依赖为无副作用替身，避免测试触发插件/感知副作用
    engine._tool_registry = MagicMock()
    engine._unified_router = MagicMock()
    engine._unified_router.should_refresh.return_value = False
    engine._tools = []

    def _boom(_msg):
        raise RuntimeError("boom")

    engine._process_message = _boom
    engine.send("hi", character="yuexinmiao")
    engine._running = True
    t = threading.Thread(target=engine._run, daemon=True)
    t.start()
    time.sleep(0.8)
    assert t.is_alive(), "引擎主循环异常后线程不应死亡"
    engine._running = False
    t.join(timeout=2)
    assert not t.is_alive()


# ───────────────────────── Fix3：launcher 业务就绪哨兵 ─────────────────────────
def test_launcher_ready_flag_path():
    import launcher
    flag = launcher._ready_flag_path(4242)
    assert flag.name == "ready_4242.flag"
    assert "logs" in flag.parts


def test_launcher_remove_ready_flag(tmp_path, monkeypatch):
    import launcher
    monkeypatch.setattr(launcher, "HERE", tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    flag = launcher._ready_flag_path(4242)
    flag.write_text("ok", encoding="utf-8")
    launcher._remove_ready_flag(4242)
    assert not flag.exists()
    # 不存在时也应安全返回
    launcher._remove_ready_flag(9999)


class _FakeChild:
    """模拟子进程：可选写就绪哨兵、运行 N 个轮询周期后退出、wait 阻塞时长。"""

    def __init__(self, pid, exit_code, ready_after_polls=None, run_polls=0,
                 wait_duration=0.0):
        self.pid = pid
        self._exit_code = exit_code
        self._ready_after = ready_after_polls  # None = 永不写哨兵
        self._run_polls = run_polls            # 死亡前 poll 次数（模拟运行时长）
        self._wait_duration = wait_duration    # wait() 阻塞时长（模拟运行到退出）
        self._polls = 0
        self.killed = False

    def poll(self):
        self._polls += 1
        if self._ready_after is not None and self._polls >= self._ready_after:
            flag = launcher_mod()._ready_flag_path(self.pid)
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("ok", encoding="utf-8")
        if self.killed:
            return self._exit_code
        if self._polls <= self._run_polls:
            return None
        return self._exit_code

    def wait(self):
        if self._wait_duration > 0:
            time.sleep(self._wait_duration)
        return self._exit_code

    def kill(self):
        self.killed = True


def launcher_mod():
    import launcher
    return launcher


def test_launcher_startup_crash_no_reset(tmp_path, monkeypatch):
    """启动期崩溃（未收到就绪哨兵）不得重置重启计数：3 次后放弃"""
    import launcher
    monkeypatch.setattr(launcher, "HERE", tmp_path)
    monkeypatch.setattr(launcher, "RESTART_DELAY", 0.0)
    monkeypatch.setattr(launcher, "READY_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(launcher, "HEALTHY_UPTIME", 0.05)
    monkeypatch.setattr(launcher, "MAX_RESTARTS_PER_WINDOW", 3)
    monkeypatch.setattr(launcher, "RESTART_WINDOW", 600.0)
    calls = {"n": 0}

    def _fake_popen(*_a, **_k):
        calls["n"] += 1
        # 启动期崩溃：跑了 10 个轮询周期（墙钟 ≥ HEALTHY_UPTIME）但从未写哨兵
        return _FakeChild(pid=1000 + calls["n"], exit_code=1,
                          ready_after_polls=None, run_polls=10)

    monkeypatch.setattr(launcher.subprocess, "Popen", _fake_popen)
    rc = launcher.main()
    assert rc == 1, "启动期反复崩溃应触发防疯转放弃"
    assert calls["n"] == 3, "启动期崩溃不重置计数：恰好 3 次后放弃（若误重置会无限重启）"


def test_launcher_healthy_run_resets_counter(tmp_path, monkeypatch):
    """收到哨兵且健康运行 ≥HEALTHY_UPTIME 才重置计数：可持续重启直到正常退出"""
    import launcher
    monkeypatch.setattr(launcher, "HERE", tmp_path)
    monkeypatch.setattr(launcher, "RESTART_DELAY", 0.0)
    monkeypatch.setattr(launcher, "READY_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(launcher, "HEALTHY_UPTIME", 0.05)
    monkeypatch.setattr(launcher, "MAX_RESTARTS_PER_WINDOW", 3)
    monkeypatch.setattr(launcher, "RESTART_WINDOW", 600.0)
    calls = {"n": 0}

    def _fake_popen(*_a, **_k):
        calls["n"] += 1
        if calls["n"] <= 3:
            # 健康崩溃：先写哨兵，wait 阻塞 0.1s（≥ HEALTHY_UPTIME）再异常退出
            return _FakeChild(pid=2000 + calls["n"], exit_code=1,
                              ready_after_polls=2, run_polls=20, wait_duration=0.1)
        return _FakeChild(pid=3000, exit_code=0, ready_after_polls=2,
                          run_polls=20, wait_duration=0.1)

    monkeypatch.setattr(launcher.subprocess, "Popen", _fake_popen)
    rc = launcher.main()
    assert rc == 0, "健康崩溃应重置计数，最终正常退出返回 0"
    assert calls["n"] == 4, "每次健康崩溃重置计数 → 第 4 次正常退出（若未重置则第 4 次前就放弃）"


def test_launcher_ready_timeout_kills_child(tmp_path, monkeypatch):
    """超过 READY_TIMEOUT 未就绪 → kill 子进程，按启动失败处理"""
    import launcher
    monkeypatch.setattr(launcher, "HERE", tmp_path)
    monkeypatch.setattr(launcher, "RESTART_DELAY", 0.0)
    monkeypatch.setattr(launcher, "READY_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(launcher, "READY_TIMEOUT", 0.06)
    monkeypatch.setattr(launcher, "HEALTHY_UPTIME", 0.05)
    monkeypatch.setattr(launcher, "MAX_RESTARTS_PER_WINDOW", 2)
    monkeypatch.setattr(launcher, "RESTART_WINDOW", 600.0)
    calls = {"n": 0}
    killed = []

    def _fake_popen(*_a, **_k):
        calls["n"] += 1
        child = _FakeChild(pid=4000 + calls["n"], exit_code=1,
                           ready_after_polls=None, run_polls=10 ** 9)
        child.kill = lambda: killed.append(child.pid) or setattr(child, "_run_polls", 0)
        return child

    monkeypatch.setattr(launcher.subprocess, "Popen", _fake_popen)
    rc = launcher.main()
    assert rc == 1, "启动超时按失败处理，反复超时应触发防疯转放弃"
    assert killed, "READY_TIMEOUT 后必须 kill 子进程"
    assert len(killed) == calls["n"] == 2, "每次超时都 kill，2 次后放弃"


# ───────────────────────── Fix4：OC_DISABLE_TRAY 判空 ─────────────────────────
def test_load_character_guards_tray_missing():
    """OC_DISABLE_TRAY=1 时 _tray 不存在，load_character 必须判空"""
    src = (ROOT / "pet.py").read_text(encoding="utf-8")
    m = re.search(r"def load_character\(self, char_id: str\):\n(.*?)(?=\n    def )", src, re.S)
    assert m, "未找到 load_character"
    body = m.group(1)
    assert 'getattr(self, "_tray", None)' in body, "缺少 _tray 判空"
    assert "tray.setIcon" in body, "托盘图标更新应走判空后的 tray 变量"


# ───────────────────────── Fix5：excepthook 链式调用 ─────────────────────────
def test_main_excepthook_chains_previous():
    """main.py 的 _hook 必须链式调用 _prev，不得直调 sys.__excepthook__"""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    m = re.search(r"def _install_excepthook\(\):\n(.*?)(?=\n_install_excepthook)", src, re.S)
    assert m, "未找到 _install_excepthook"
    body = m.group(1)
    assert "_prev = getattr(sys, \"excepthook\", sys.__excepthook__)" in body, (
        "必须保存前一个钩子（crash_collector 的收集钩子）"
    )
    assert "sys.__excepthook__(etype, exc, tb)" not in body, (
        "直调 sys.__excepthook__ 会跳过 crash_collector 收集钩子"
    )
    assert "_prev(etype, exc, tb)" in body, "必须链式调用 _prev"


# ───────────────────────── Fix6：CosyVoice 超时脏队列 ─────────────────────────
def test_cosyvoice_synth_timeout_relaxed():
    """CPU 环境下 SYNTH_TIMEOUT 放宽到 300s（原 180s 偏紧）"""
    from tts_provider import cosyvoice as mod
    assert mod.SYNTH_TIMEOUT >= 300.0


def test_cosyvoice_drain_replies_helper():
    """_drain_replies 清空队列并返回丢弃条数"""
    from tts_provider.cosyvoice import CosyVoiceProvider
    p = CosyVoiceProvider()
    p._replies.put({"id": 1})
    p._replies.put(None)
    p._replies.put({"id": 2})
    assert p._drain_replies() == 3
    assert p._replies.empty()


def test_cosyvoice_timeout_drains_replies_and_resets_loaded():
    """超时分支：置 _loaded=False 且清空陈旧响应（防脏队列假死）"""
    from tts_provider.cosyvoice import CosyVoiceProvider
    p = CosyVoiceProvider()

    class _FakeStdin:
        def write(self, _s):
            pass

        def flush(self):
            pass

    class _FakeProc:
        stdin = _FakeStdin()

        def poll(self):
            return None

    p._proc = _FakeProc()
    p._loaded = True
    # 预置一条陈旧响应（错误 id），模拟 worker 迟到的上一句响应
    p._replies.put({"id": 999, "ok": True})
    resp = p._request({"cmd": "synth", "text": "hi"}, timeout=0.01)
    assert resp is None
    assert p._loaded is False, "超时后必须置 _loaded=False（下次 preload 重新加载）"
    assert p._replies.empty(), "超时后必须清空响应队列（防脏队列配错 id）"
    p._closing = True
    p._proc = None


if __name__ == "__main__":
    # 允许直接 python tests/test_fix_batch1.py 运行
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nALL FIX-BATCH-1 CHECKS PASSED ({len(fns)} tests)")
